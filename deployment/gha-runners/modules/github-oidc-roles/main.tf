# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# GitHub Actions OIDC provider (optional) plus roles whose trust is pinned to exact `sub` claims.

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

locals {
  issuer_host = "token.actions.githubusercontent.com"
}

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_provider ? 1 : 0

  url            = "https://${local.issuer_host}"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  provider_arn = (
    var.create_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/${local.issuer_host}"
  )
}

data "aws_iam_policy_document" "trust" {
  for_each = var.roles

  statement {
    sid     = "GitHubActionsOIDC"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity", "sts:TagSession"]

    principals {
      type        = "Federated"
      identifiers = [local.provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.issuer_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.issuer_host}:sub"
      values   = each.value.subjects
    }
  }
}

resource "aws_iam_role" "this" {
  for_each = var.roles

  name                 = each.value.name
  description          = each.value.description
  assume_role_policy   = data.aws_iam_policy_document.trust[each.key].json
  max_session_duration = each.value.max_session_duration
  permissions_boundary = var.permissions_boundary_arn

  lifecycle {
    precondition {
      condition     = length(each.value.subjects) > 0 && alltrue([for s in each.value.subjects : !strcontains(s, "*")])
      error_message = "OIDC role ${each.value.name} must trust at least one exact sub claim (no wildcards)."
    }
  }
}

resource "aws_iam_role_policy" "this" {
  for_each = { for k, r in var.roles : k => r if r.policy_json != null }

  name   = "${each.value.name}-policy"
  role   = aws_iam_role.this[each.key].id
  policy = each.value.policy_json
}

resource "aws_iam_role_policy_attachment" "this" {
  for_each = merge([
    for k, r in var.roles : { for arn in r.managed_policy_arns : "${k}|${arn}" => { role = k, arn = arn } }
  ]...)

  role       = aws_iam_role.this[each.value.role].name
  policy_arn = each.value.arn
}
