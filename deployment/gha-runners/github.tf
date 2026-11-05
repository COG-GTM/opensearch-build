# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# GitHub side of ADR 0001: the org runner group (restricted to this repository), the
# ci / benchmark / release environments, and the role-ARN secrets (names per the ADR secret list).
# Disabled with manage_github = false (e.g. a sandbox plan without an org-admin token).

data "github_repository" "this" {
  count     = var.manage_github ? 1 : 0
  full_name = local.repository_full_name
}

resource "github_actions_runner_group" "this" {
  count = var.manage_github ? 1 : 0

  name                    = var.runner_group_name
  visibility              = "selected"
  selected_repository_ids = [data.github_repository.this[0].repo_id]
  # The repository is public; access is limited to it by selected_repository_ids, and fork PRs never target these labels (ADR 0001).
  allows_public_repositories = true
}

locals {
  environments = {
    ci        = { reviewers = false, tags = false }
    benchmark = { reviewers = false, tags = false }
    release   = { reviewers = true, tags = true }
  }

  # Secret name => environment (null = repository-level), per the ADR "Secret list for p2".
  role_secret_scope = {
    AWS_ROLE_METRICS      = null
    AWS_ROLE_TEST_RESULTS = null
    AWS_ROLE_ARTIFACTS    = null
    AWS_ROLE_PACKER       = null
    AWS_ROLE_BENCHMARK    = "benchmark"
    AWS_ROLE_PROMOTION    = "release"
    AWS_ROLE_RPM_SIGNING  = "release"
    AWS_ROLE_SIGNING_BASE = "release"
  }
}

data "github_team" "release_reviewers" {
  for_each = var.manage_github ? toset(var.release_reviewers.teams) : toset([])
  slug     = each.value
}

data "github_user" "release_reviewers" {
  for_each = var.manage_github ? toset(var.release_reviewers.users) : toset([])
  username = each.value
}

resource "github_repository_environment" "this" {
  for_each = var.manage_github ? local.environments : {}

  repository          = var.github_repository
  environment         = each.key
  prevent_self_review = each.value.reviewers

  dynamic "reviewers" {
    for_each = each.value.reviewers ? [1] : []
    content {
      teams = [for t in data.github_team.release_reviewers : t.id]
      users = [for u in data.github_user.release_reviewers : u.id]
    }
  }

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }

  lifecycle {
    precondition {
      condition     = !each.value.reviewers || length(var.release_reviewers.teams) + length(var.release_reviewers.users) > 0
      error_message = "The release environment needs at least one required reviewer (var.release_reviewers)."
    }
  }
}

resource "github_repository_environment_deployment_policy" "main" {
  for_each = var.manage_github ? local.environments : {}

  repository     = var.github_repository
  environment    = github_repository_environment.this[each.key].environment
  branch_pattern = "main"
}

resource "github_repository_environment_deployment_policy" "tags" {
  for_each = var.manage_github ? { for k, v in local.environments : k => v if v.tags } : {}

  repository  = var.github_repository
  environment = github_repository_environment.this[each.key].environment
  tag_pattern = "*"
}

# Role ARNs are identifiers, not credentials; they are stored as secrets only because the ADR
# keeps every AWS reference behind `secrets.NAME`.
resource "github_actions_secret" "role_arn" {
  for_each = var.manage_github ? { for k, env in local.role_secret_scope : k => env if env == null } : {}

  repository      = var.github_repository
  secret_name     = each.key
  plaintext_value = local.oidc_role_arns[each.key]
}

resource "github_actions_environment_secret" "role_arn" {
  for_each = var.manage_github ? { for k, env in local.role_secret_scope : k => env if env != null } : {}

  repository      = var.github_repository
  environment     = github_repository_environment.this[each.value].environment
  secret_name     = each.key
  plaintext_value = local.oidc_role_arns[each.key]
}
