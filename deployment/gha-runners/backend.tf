# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Partial configuration: bucket, key, region and lock table are passed with
# `terraform init -backend-config=...` (see README). scripts/plan-sandbox.sh
# overrides this with a local backend so a sandbox plan never touches shared state.
terraform {
  backend "s3" {}
}
