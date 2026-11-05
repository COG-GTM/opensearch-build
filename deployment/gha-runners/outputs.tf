# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

output "runner_pools" {
  description = "Pool key => runs-on labels, for checking against the ADR 0001 label map."
  value       = { for k, p in local.runner_pools : k => p.labels }
}

output "webhook_endpoint" {
  description = "URL to set as the GitHub App webhook (workflow_job events)."
  value       = module.runners.webhook.endpoint
}

output "oidc_role_arns" {
  description = "Secret name => role ARN for every OIDC role."
  value       = local.oidc_role_arns
}

output "oidc_provider_arns" {
  description = "GitHub OIDC provider ARN per account."
  value = {
    runner      = module.oidc_runner.provider_arn
    metrics     = module.oidc_metrics.provider_arn
    public      = module.oidc_public.provider_arn
    production  = module.oidc_production.provider_arn
    rpm_signing = module.oidc_rpm_signing.provider_arn
    perf        = module.oidc_perf.provider_arn
    public_lf   = module.oidc_public_lf.provider_arn
  }
}
