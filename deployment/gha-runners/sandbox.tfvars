# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Plan-only sandbox inputs (scripts/plan-sandbox.sh). Single account, AWS public AMIs standing in
# for the Jenkins AMIs, placeholder ARNs for resources that only exist in the shared accounts.
# Never apply with this file.
manage_github = false
prefix        = "osb-sbx"

create_oidc_provider = {
  runner      = true
  metrics     = false
  public      = false
  production  = false
  rpm_signing = false
  perf        = false
  public_lf   = false
}

runner_ami_ssm_parameters = {
  "al2023-x64-m5-4xlarge-docker-host"           = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
  "al2023-arm64-m6g-4xlarge-docker-host"        = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
  "windows2019-x64-m5-4xlarge-docker-host"      = "/aws/service/ami-windows-latest/Windows_Server-2019-English-Full-Base"
  "windows2019-x64-m5-4xlarge-docker-builder"   = "/aws/service/ami-windows-latest/Windows_Server-2019-English-Full-Base"
  "ubuntu2404-x64-m5-2xlarge-docker-builder"    = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
  "al2023-x64-c5-4xlarge-single-host"           = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
  "al2023-arm64-c6g-4xlarge-single-host"        = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
  "al2023-x64-m5-2xlarge-benchmark-test"        = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
  "al2023-x64-m5-2xlarge-vector-benchmark-test" = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
  "macos14-x64-mac1"                            = "/aws/service/ec2-macos/sonoma/x86_64_mac/latest/image_id"
  "macos14-arm64-mac2"                          = "/aws/service/ec2-macos/sonoma/arm64_mac/latest/image_id"
}

metrics_opensearch_domain_arn          = "arn:aws:es:us-east-1:111111111111:domain/sandbox-metrics"
artifacts_bucket_name                  = "sandbox-opensearch-build-artifacts"
production_bucket_name                 = "sandbox-opensearch-build-production"
production_cloudfront_distribution_arn = "arn:aws:cloudfront::111111111111:distribution/ESANDBOX0000"
ecr_public_repository_arns             = ["arn:aws:ecr-public::111111111111:repository/sandbox-opensearch"]
rpm_signing_secret_arns                = ["arn:aws:secretsmanager:us-east-1:111111111111:secret:sandbox-rpm-signing-AbCdEf"]
signer_role_arns                       = ["arn:aws:iam::111111111111:role/sandbox-signer"]
perf_cdk_role_arn_pattern              = "arn:aws:iam::111111111111:role/cdk-*"
