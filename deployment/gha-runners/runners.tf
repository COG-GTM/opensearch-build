# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Ephemeral, scale-from-zero self-hosted runners (ADR 0001, `d-runners`), built on
# github-aws-runners/terraform-aws-github-runner. Every pool: one job per instance (JIT config),
# no warm pool / idle config, instances created on `workflow_job` queued events and terminated
# after the job.

data "aws_caller_identity" "runner" {}
data "aws_partition" "runner" {}

locals {
  repository_full_name = "${var.github_owner}/${var.github_repository}"

  runner_pools = yamldecode(file("${path.module}/runner-pools.yaml"))

  # Pools boot the agent baked into their AMI by deployment/packer (no module install_runner, no binaries
  # syncer). The packer pin is the minimum agent version a pool accepts; older AMIs terminate at boot.
  min_runner_version = jsondecode(file("${path.module}/../packer/gha-runner/runner-version.json")).version

  userdata_template = {
    for k, p in local.runner_pools : k => (
      p.os == "windows" ? "${path.module}/templates/user-data-windows.ps1" :
      p.os == "osx" ? "${path.module}/templates/user-data-osx.sh" :
      try(p.run_as, "ec2-user") == "ubuntu" ? "${path.module}/templates/user-data-ubuntu.sh" :
      "${path.module}/templates/user-data-al2023.sh"
    )
  }

  baked_runner_check = {
    for os, ext in { linux = "sh", osx = "sh", windows = "ps1" } : os => templatefile(
      "${path.module}/templates/baked-runner.${ext}.tftpl", { min_runner_version = local.min_runner_version }
    )
  }

  ssm_arn_prefix = "arn:${data.aws_partition.runner.partition}:ssm:${var.aws_region}"

  # Public AWS parameters (/aws/service/...) have no account in their ARN.
  ami_ssm_parameter_arns = {
    for pool, name in var.runner_ami_ssm_parameters : pool => (
      startswith(name, "/aws/service/")
      ? "${local.ssm_arn_prefix}::parameter${name}"
      : "${local.ssm_arn_prefix}:${data.aws_caller_identity.runner.account_id}:parameter${name}"
    )
  }

  github_app_ssm_arn = {
    for k, name in var.github_app : k => "${local.ssm_arn_prefix}:${data.aws_caller_identity.runner.account_id}:parameter${name}"
  }

  mac_pools = { for k, p in local.runner_pools : k => p if p.os == "osx" }

  mac_hosts = merge([
    for pool, azs in merge({ for k in keys(local.mac_pools) : k => ["${var.aws_region}a"] }, var.mac_dedicated_host_azs) : {
      for i, az in azs : "${pool}-${i}" => {
        pool              = pool
        availability_zone = az
        instance_type     = local.runner_pools[pool].instance_type
      }
    } if contains(keys(local.mac_pools), pool)
  ]...)

  # AL2023 roots on /dev/xvda; Ubuntu, Windows and macOS AMIs root on /dev/sda1.
  root_device = {
    for k, p in local.runner_pools : k => (
      p.os == "linux" && try(p.run_as, "ec2-user") == "ec2-user" ? "/dev/xvda" : "/dev/sda1"
    )
  }

  multi_runner_config = {
    for k, p in local.runner_pools : p.id => {
      matcherConfig = {
        labelMatchers = [p.labels]
        # Job labels must equal the pool's label set (not a subset), so e.g. [self-hosted, linux, x64] matches nothing.
        exactMatch              = true
        bidirectionalLabelMatch = true
      }
      fifo = true
      runner_config = {
        runner_os           = p.os
        runner_architecture = p.arch
        runner_name_prefix  = "${p.id}-"
        runner_run_as       = p.os == "windows" ? null : try(p.run_as, "ec2-user")

        enable_runner_binaries_syncer = false
        enable_userdata               = true
        userdata_template             = local.userdata_template[k]
        userdata_post_install         = local.baked_runner_check[p.os]

        # The pool label is the only extra label; GitHub adds self-hosted/<os>/<arch>.
        runner_extra_labels = [for l in p.labels : l if !contains(["self-hosted", "linux", "windows", "macos", "x64", "arm64"], l)]

        enable_organization_runners = true
        runner_group_name           = var.runner_group_name

        enable_ephemeral_runners        = true
        enable_jit_config               = true
        enable_job_queued_check         = true
        delay_webhook_event             = 0
        job_queue_retention_in_seconds  = 3600
        runners_maximum_count           = lookup(var.runner_max_count_overrides, k, p.max_runners)
        idle_config                     = []
        scale_down_schedule_expression  = "cron(*/5 * * * ? *)"
        minimum_running_time_in_minutes = p.os == "osx" ? 60 : (p.os == "windows" ? 15 : 5)
        runner_boot_time_in_minutes     = p.os == "osx" ? 30 : (p.os == "windows" ? 15 : 5)

        instance_types                = [p.instance_type]
        instance_target_capacity_type = "on-demand"
        ebs_optimized                 = true

        ami = {
          id_ssm_parameter_arn = local.ami_ssm_parameter_arns[k]
        }

        block_device_mappings = [{
          device_name = local.root_device[k]
          volume_size = p.volume_size_gb
          volume_type = "gp3"
          encrypted   = true
        }]

        vpc_id     = try(p.network, "") == "benchmark" ? coalesce(var.benchmark_vpc_id, var.vpc_id) : var.vpc_id
        subnet_ids = try(p.network, "") == "benchmark" && length(var.benchmark_subnet_ids) > 0 ? var.benchmark_subnet_ids : var.subnet_ids

        # Instance profile: boot-time only (SSM, CloudWatch agent, ECR pulls). Jobs use OIDC roles.
        enable_ssm_on_runners               = true
        enable_cloudwatch_agent             = true
        runner_iam_role_managed_policy_arns = ["arn:${data.aws_partition.runner.partition}:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"]

        runner_metadata_options = {
          # Tags carry no secrets; the macOS start script reads its config only from IMDS tags.
          instance_metadata_tags = "enabled"
          http_endpoint          = "enabled"
          http_tokens            = "required"
          # 1 hop: job containers on the Docker bridge cannot reach IMDS / the instance profile.
          http_put_response_hop_limit = 1
        }

        runner_ec2_tags = {
          "opensearch-build:runner-pool"   = k
          "opensearch-build:jenkins-label" = p.jenkins_label
        }

        # EC2 Mac pools launch onto the dedicated hosts below.
        use_dedicated_host = p.os == "osx"
        placement = p.os == "osx" ? {
          tenancy                 = "host"
          host_resource_group_arn = aws_resourcegroups_group.mac_hosts[k].arn
        } : null
        license_specifications = [
          for arn in(p.os == "osx" ? [aws_licensemanager_license_configuration.mac_hosts.arn] : []) : {
            license_configuration_arn = arn
          }
        ]
      }
    }
  }
}

# Fails the plan early if a pool's AMI parameter has not been published yet.
data "aws_ssm_parameter" "runner_ami" {
  for_each = local.runner_pools
  name     = var.runner_ami_ssm_parameters[each.key]
}

module "runners" {
  source  = "github-aws-runners/github-runner/aws//modules/multi-runner"
  version = "8.1.0"

  prefix     = var.prefix
  aws_region = var.aws_region
  vpc_id     = var.vpc_id
  subnet_ids = var.subnet_ids
  tags       = local.default_tags

  github_app = {
    id_ssm = {
      name = var.github_app.id_ssm_parameter_name
      arn  = local.github_app_ssm_arn.id_ssm_parameter_name
    }
    key_base64_ssm = {
      name = var.github_app.key_base64_ssm_parameter_name
      arn  = local.github_app_ssm_arn.key_base64_ssm_parameter_name
    }
    webhook_secret_ssm = {
      name = var.github_app.webhook_secret_ssm_parameter_name
      arn  = local.github_app_ssm_arn.webhook_secret_ssm_parameter_name
    }
  }

  multi_runner_config   = local.multi_runner_config
  repository_white_list = [local.repository_full_name]

  eventbridge = {
    enable        = true
    accept_events = ["workflow_job"]
  }

  instance_termination_watcher = {
    enable = true
    zip    = "${var.runner_lambda_zip_dir}/termination-watcher.zip"
  }

  webhook_lambda_zip                = "${var.runner_lambda_zip_dir}/webhook.zip"
  runners_lambda_zip                = "${var.runner_lambda_zip_dir}/runners.zip"
  runner_binaries_syncer_lambda_zip = "${var.runner_lambda_zip_dir}/runner-binaries-syncer.zip"

  logging_retention_in_days = var.log_retention_days
}

# EC2 Mac pools: dedicated hosts in a host resource group so the ephemeral runner launches onto them.
resource "aws_ec2_host" "mac" {
  for_each = local.mac_hosts

  instance_type     = each.value.instance_type
  availability_zone = each.value.availability_zone
  auto_placement    = "on"

  tags = {
    Name                           = "${var.prefix}-${each.key}"
    "opensearch-build:runner-pool" = each.value.pool
  }
}

resource "aws_resourcegroups_group" "mac_hosts" {
  for_each = local.mac_pools

  name = "${var.prefix}-${each.key}"

  configuration {
    type = "AWS::EC2::HostManagement"
    parameters {
      name   = "any-host-based-license-configuration"
      values = ["true"]
    }
    parameters {
      name   = "auto-allocate-host"
      values = ["false"]
    }
    parameters {
      name   = "auto-host-recovery"
      values = ["false"]
    }
    parameters {
      name   = "auto-release-host"
      values = ["false"]
    }
  }

  configuration {
    type = "AWS::ResourceGroups::Generic"
    parameters {
      name   = "allowed-resource-types"
      values = ["AWS::EC2::Host"]
    }
    parameters {
      name   = "deletion-protection"
      values = ["UNLESS_EMPTY"]
    }
  }
}

resource "aws_resourcegroups_resource" "mac_hosts" {
  for_each = local.mac_hosts

  group_arn    = aws_resourcegroups_group.mac_hosts[each.value.pool].arn
  resource_arn = aws_ec2_host.mac[each.key].arn
}

resource "aws_licensemanager_license_configuration" "mac_hosts" {
  name                  = "${var.prefix}-mac-dedicated-hosts"
  description           = "License configuration for EC2 Mac dedicated hosts used by GitHub Actions runners"
  license_counting_type = "Socket"
}
