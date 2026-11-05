# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

output "provider_arn" {
  description = "ARN of the GitHub Actions OIDC provider used in the trust policies."
  value       = local.provider_arn
}

output "role_arns" {
  description = "Role ARNs keyed like var.roles."
  value       = { for k, r in aws_iam_role.this : k => r.arn }
}
