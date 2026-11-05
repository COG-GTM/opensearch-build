# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

variable "create_provider" {
  description = "Create the token.actions.githubusercontent.com OIDC provider in this account (false = it already exists)."
  type        = bool
}

variable "roles" {
  description = "Roles to create, keyed by the GitHub secret name that will hold the role ARN."
  type = map(object({
    name                 = string
    description          = string
    subjects             = list(string)
    max_session_duration = optional(number, 3600)
    policy_json          = optional(string)
    managed_policy_arns  = optional(list(string), [])
  }))
}

variable "permissions_boundary_arn" {
  description = "Optional permissions boundary applied to every role."
  type        = string
  default     = null
}
