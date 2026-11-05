#!/usr/bin/env bash

# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Plan (never apply) against a sandbox account with a throwaway local backend.
# Uses the caller's AWS credentials and the sandbox's default VPC; writes plan output to ./out/.
set -euo pipefail

cd "$(dirname "$0")/.."
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="${AWS_REGION}"

cat > sandbox_backend_override.tf <<'HCL'
terraform {
  backend "local" {
    path = "out/sandbox.tfstate"
  }
}
HCL
trap 'rm -f sandbox_backend_override.tf' EXIT

[ -f lambdas/webhook.zip ] || scripts/download-lambdas.sh

vpc_id="$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)"
subnets="$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=${vpc_id}" --query 'Subnets[].SubnetId' --output json | tr -d ' \n')"

mkdir -p out
terraform init -input=false -reconfigure
terraform validate | tee out/validate.txt
terraform plan -input=false -lock=false -refresh=true \
  -var-file=sandbox.tfvars \
  -var "aws_region=${AWS_REGION}" \
  -var "vpc_id=${vpc_id}" \
  -var "subnet_ids=${subnets}" \
  -out=out/sandbox.tfplan | tee out/plan.txt
terraform show -no-color out/sandbox.tfplan > out/plan-show.txt
echo "Plan written to out/plan.txt (binary plan out/sandbox.tfplan is NOT for apply)."
