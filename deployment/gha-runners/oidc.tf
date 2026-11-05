# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# AWS IAM OIDC provider + roles for GitHub Actions (ADR 0001, "AWS access").
# Trust is pinned to exact `sub` claims of COG-GTM/opensearch-build:
#   - build/read roles:   main branch and the `ci` environment
#   - benchmark role:     the `benchmark` environment only
#   - release roles:      the `release` environment only (required reviewers)
#   - AWS_ROLE_ARTIFACTS / AWS_ROLE_TEST_RESULTS also trust release / benchmark, per the ADR role table
# Role names are new (gha-opensearch-build-*) so they never collide with the Jenkins roles
# that keep running until cutover.

locals {
  sub_prefix = "repo:${local.repository_full_name}"
  subjects = {
    build     = ["${local.sub_prefix}:ref:refs/heads/main", "${local.sub_prefix}:environment:ci"]
    benchmark = ["${local.sub_prefix}:environment:benchmark"]
    release   = ["${local.sub_prefix}:environment:release"]
  }
  role_name_prefix = "gha-${var.github_repository}"

  artifacts_bucket_arn  = "arn:${data.aws_partition.runner.partition}:s3:::${var.artifacts_bucket_name}"
  production_bucket_arn = "arn:${data.aws_partition.runner.partition}:s3:::${var.production_bucket_name}"
}

# --- read/build roles --------------------------------------------------------

data "aws_iam_policy_document" "metrics" {
  statement {
    sid       = "IndexBuildAndTestMetrics"
    actions   = ["es:ESHttpGet", "es:ESHttpHead", "es:ESHttpPost", "es:ESHttpPut"]
    resources = ["${var.metrics_opensearch_domain_arn}/*"]
  }
}

data "aws_iam_policy_document" "artifacts" {
  statement {
    sid       = "ListArtifactsBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.artifacts_bucket_arn]
  }
  statement {
    sid       = "ReadWriteCiBundles"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${local.artifacts_bucket_arn}/*"]
  }
}

data "aws_iam_policy_document" "test_results" {
  statement {
    sid       = "ListArtifactsBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.artifacts_bucket_arn]
  }
  statement {
    sid       = "ReadBundles"
    actions   = ["s3:GetObject"]
    resources = ["${local.artifacts_bucket_arn}/*"]
  }
  statement {
    sid     = "WriteTestResults"
    actions = ["s3:PutObject", "s3:AbortMultipartUpload"]
    resources = [
      "${local.artifacts_bucket_arn}/*/test-results/*",
      "${local.artifacts_bucket_arn}/*/test-report.yml",
    ]
  }
}

data "aws_iam_policy_document" "packer" {
  # Minimal permission set documented by the Packer amazon-ebs builder.
  statement {
    sid = "PackerAmazonEbs"
    actions = [
      "ec2:AttachVolume", "ec2:AuthorizeSecurityGroupIngress", "ec2:CopyImage", "ec2:CreateImage",
      "ec2:CreateKeyPair", "ec2:CreateSecurityGroup", "ec2:CreateSnapshot", "ec2:CreateTags",
      "ec2:CreateVolume", "ec2:DeleteKeyPair", "ec2:DeleteSecurityGroup", "ec2:DeleteSnapshot",
      "ec2:DeleteVolume", "ec2:DeregisterImage", "ec2:DescribeImageAttribute", "ec2:DescribeImages",
      "ec2:DescribeInstances", "ec2:DescribeInstanceStatus", "ec2:DescribeRegions",
      "ec2:DescribeSecurityGroups", "ec2:DescribeSnapshots", "ec2:DescribeSubnets", "ec2:DescribeTags",
      "ec2:DescribeVolumes", "ec2:DescribeVpcs", "ec2:DetachVolume", "ec2:GetPasswordData",
      "ec2:ModifyImageAttribute", "ec2:ModifyInstanceAttribute", "ec2:ModifySnapshotAttribute",
      "ec2:RegisterImage", "ec2:RunInstances", "ec2:StopInstances", "ec2:TerminateInstances",
    ]
    resources = ["*"]
  }
}

# --- benchmark role ------------------------------------------------------------

data "aws_iam_policy_document" "benchmark" {
  statement {
    sid       = "DeployBenchmarkClustersViaCdkBootstrapRoles"
    actions   = ["sts:AssumeRole", "sts:TagSession"]
    resources = [var.perf_cdk_role_arn_pattern]
  }
}

# --- release roles -------------------------------------------------------------

data "aws_iam_policy_document" "promotion" {
  statement {
    sid       = "ListProductionBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.production_bucket_arn]
  }
  statement {
    sid       = "PromoteToProductionBucket"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${local.production_bucket_arn}/*"]
  }
  statement {
    sid       = "InvalidateProductionCdn"
    actions   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
    resources = [var.production_cloudfront_distribution_arn]
  }
  dynamic "statement" {
    for_each = length(var.ecr_public_repository_arns) > 0 ? [1] : []
    content {
      sid = "PushReleaseImages"
      actions = [
        "ecr-public:BatchCheckLayerAvailability", "ecr-public:CompleteLayerUpload",
        "ecr-public:InitiateLayerUpload", "ecr-public:PutImage", "ecr-public:UploadLayerPart",
      ]
      resources = var.ecr_public_repository_arns
    }
  }
  dynamic "statement" {
    for_each = length(var.ecr_public_repository_arns) > 0 ? [1] : []
    content {
      sid       = "EcrPublicLogin"
      actions   = ["ecr-public:GetAuthorizationToken", "sts:GetServiceBearerToken"]
      resources = ["*"]
    }
  }
}

data "aws_iam_policy_document" "rpm_signing" {
  statement {
    sid       = "ReadRpmSigningMaterial"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.rpm_signing_secret_arns
  }
}

data "aws_iam_policy_document" "signing_base" {
  statement {
    sid       = "ChainIntoSignerRoles"
    actions   = ["sts:AssumeRole", "sts:TagSession"]
    resources = var.signer_role_arns
  }
}

# --- one module call per account (ADR 0001 role table) ---------------------------

module "oidc_runner" {
  source          = "./modules/github-oidc-roles"
  create_provider = var.create_oidc_provider.runner
  roles = {
    AWS_ROLE_SIGNING_BASE = {
      name        = "${local.role_name_prefix}-signing-base"
      description = "GHA release: chain into PGP/Windows/macOS signer roles (replaces sign-artifacts base role)"
      subjects    = local.subjects.release
      policy_json = data.aws_iam_policy_document.signing_base.json
    }
  }
}

module "oidc_metrics" {
  source          = "./modules/github-oidc-roles"
  providers       = { aws = aws.metrics }
  create_provider = var.create_oidc_provider.metrics
  roles = {
    AWS_ROLE_METRICS = {
      name        = "${local.role_name_prefix}-metrics"
      description = "GHA build/read: index build and test metrics (replaces OpenSearchJenkinsAccessRole)"
      subjects    = local.subjects.build
      policy_json = data.aws_iam_policy_document.metrics.json
    }
  }
}

module "oidc_public" {
  source          = "./modules/github-oidc-roles"
  providers       = { aws = aws.public }
  create_provider = var.create_oidc_provider.public
  roles = {
    AWS_ROLE_ARTIFACTS = {
      name        = "${local.role_name_prefix}-artifacts"
      description = "GHA build/read: read and write CI bundles (replaces opensearch-bundle)"
      subjects    = concat(local.subjects.build, local.subjects.release)
      policy_json = data.aws_iam_policy_document.artifacts.json
    }
    AWS_ROLE_TEST_RESULTS = {
      name        = "${local.role_name_prefix}-test-results"
      description = "GHA build/read: upload integration and benchmark test results (replaces opensearch-test)"
      subjects    = concat(local.subjects.build, local.subjects.benchmark)
      policy_json = data.aws_iam_policy_document.test_results.json
    }
  }
}

module "oidc_production" {
  source          = "./modules/github-oidc-roles"
  providers       = { aws = aws.production }
  create_provider = var.create_oidc_provider.production
  roles = {
    AWS_ROLE_PROMOTION = {
      name        = "${local.role_name_prefix}-release-promotion"
      description = "GHA release environment: promote artifacts to production (replaces jenkins-artifact-promotion-role)"
      subjects    = local.subjects.release
      policy_json = data.aws_iam_policy_document.promotion.json
    }
  }
}

module "oidc_rpm_signing" {
  source          = "./modules/github-oidc-roles"
  providers       = { aws = aws.rpm_signing }
  create_provider = var.create_oidc_provider.rpm_signing
  roles = {
    AWS_ROLE_RPM_SIGNING = {
      name        = "${local.role_name_prefix}-rpm-signing"
      description = "GHA release environment: read RPM signing material (replaces jenkins-prod-rpm-signing-assume-role)"
      subjects    = local.subjects.release
      policy_json = data.aws_iam_policy_document.rpm_signing.json
    }
  }
}

module "oidc_perf" {
  source          = "./modules/github-oidc-roles"
  providers       = { aws = aws.perf }
  create_provider = var.create_oidc_provider.perf
  roles = {
    AWS_ROLE_BENCHMARK = {
      name                 = "${local.role_name_prefix}-benchmark"
      description          = "GHA benchmark environment: deploy benchmark clusters via CDK (replaces jenkins full-access-nightlies)"
      subjects             = local.subjects.benchmark
      max_session_duration = 43200
      policy_json          = data.aws_iam_policy_document.benchmark.json
    }
  }
}

module "oidc_public_lf" {
  source          = "./modules/github-oidc-roles"
  providers       = { aws = aws.public_lf }
  create_provider = var.create_oidc_provider.public_lf
  roles = {
    AWS_ROLE_PACKER = {
      name        = "${local.role_name_prefix}-packer"
      description = "GHA build: build the runner/Jenkins AMIs with Packer"
      subjects    = local.subjects.build
      policy_json = data.aws_iam_policy_document.packer.json
    }
  }
}

locals {
  oidc_role_arns = merge(
    module.oidc_runner.role_arns,
    module.oidc_metrics.role_arns,
    module.oidc_public.role_arns,
    module.oidc_production.role_arns,
    module.oidc_rpm_signing.role_arns,
    module.oidc_perf.role_arns,
    module.oidc_public_lf.role_arns,
  )
}
