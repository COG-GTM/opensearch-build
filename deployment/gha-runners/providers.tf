# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

locals {
  default_tags = merge(var.tags, {
    "opensearch-build:component" = "gha-runners"
    "opensearch-build:adr"       = "0001"
    "managed-by"                 = "terraform"
  })
}

# Runner account: the account that runs the Jenkins agents today.
provider "aws" {
  region = var.aws_region

  dynamic "assume_role" {
    for_each = var.account_role_arns.runner == null ? [] : [var.account_role_arns.runner]
    content {
      role_arn     = assume_role.value
      session_name = "terraform-gha-runners"
    }
  }

  default_tags {
    tags = local.default_tags
  }
}

# One aliased provider per account that owns a role in the ADR 0001 role table.
# A null entry in var.account_role_arns uses the caller's credentials (single-account sandbox).
provider "aws" {
  alias  = "metrics"
  region = var.aws_region

  dynamic "assume_role" {
    for_each = var.account_role_arns.metrics == null ? [] : [var.account_role_arns.metrics]
    content {
      role_arn     = assume_role.value
      session_name = "terraform-gha-runners"
    }
  }

  default_tags {
    tags = local.default_tags
  }
}

provider "aws" {
  alias  = "public"
  region = var.aws_region

  dynamic "assume_role" {
    for_each = var.account_role_arns.public == null ? [] : [var.account_role_arns.public]
    content {
      role_arn     = assume_role.value
      session_name = "terraform-gha-runners"
    }
  }

  default_tags {
    tags = local.default_tags
  }
}

provider "aws" {
  alias  = "production"
  region = var.aws_region

  dynamic "assume_role" {
    for_each = var.account_role_arns.production == null ? [] : [var.account_role_arns.production]
    content {
      role_arn     = assume_role.value
      session_name = "terraform-gha-runners"
    }
  }

  default_tags {
    tags = local.default_tags
  }
}

provider "aws" {
  alias  = "rpm_signing"
  region = var.aws_region

  dynamic "assume_role" {
    for_each = var.account_role_arns.rpm_signing == null ? [] : [var.account_role_arns.rpm_signing]
    content {
      role_arn     = assume_role.value
      session_name = "terraform-gha-runners"
    }
  }

  default_tags {
    tags = local.default_tags
  }
}

provider "aws" {
  alias  = "perf"
  region = var.aws_region

  dynamic "assume_role" {
    for_each = var.account_role_arns.perf == null ? [] : [var.account_role_arns.perf]
    content {
      role_arn     = assume_role.value
      session_name = "terraform-gha-runners"
    }
  }

  default_tags {
    tags = local.default_tags
  }
}

provider "aws" {
  alias  = "public_lf"
  region = var.aws_region

  dynamic "assume_role" {
    for_each = var.account_role_arns.public_lf == null ? [] : [var.account_role_arns.public_lf]
    content {
      role_arn     = assume_role.value
      session_name = "terraform-gha-runners"
    }
  }

  default_tags {
    tags = local.default_tags
  }
}

# Token comes from GITHUB_TOKEN (org admin for the runner group, repo admin for environments/secrets).
provider "github" {
  owner = var.github_owner
}
