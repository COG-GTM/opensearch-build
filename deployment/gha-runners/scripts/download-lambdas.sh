#!/usr/bin/env bash

# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Download the control-plane lambda zips that match the pinned module version in runners.tf.
set -euo pipefail

cd "$(dirname "$0")/.."
version="$(sed -n 's/^ *version *= *"\([0-9.]*\)".*/\1/p' runners.tf | head -1)"
mkdir -p lambdas
for name in webhook runners runner-binaries-syncer termination-watcher; do
  curl -fsSL -o "lambdas/${name}.zip" \
    "https://github.com/github-aws-runners/terraform-aws-github-runner/releases/download/v${version}/${name}.zip"
done
echo "Downloaded lambdas for module v${version}"
