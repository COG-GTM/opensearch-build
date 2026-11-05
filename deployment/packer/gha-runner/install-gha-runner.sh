#!/bin/bash

# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Bakes the GitHub Actions runner agent into a Linux or macOS agent AMI (ADR 0001). Runs as root from
# the packer shell provisioner added by render_template.py. Nothing is registered here: the ephemeral
# runner gets its JIT config at boot from the terraform-aws-github-runner start script.

set -euo pipefail

: "${RUNNER_VERSION:?}" "${RUNNER_PLATFORM:?}" "${RUNNER_SHA256:?}" "${RUNNER_USER:?}"

runner_dir=/opt/actions-runner
tarball="actions-runner-${RUNNER_PLATFORM}-${RUNNER_VERSION}.tar.gz"

mkdir -p "$runner_dir" /opt/hostedtoolcache
cd "$runner_dir"
curl -fsSL --retry 5 -o "$tarball" "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${tarball}"
if command -v sha256sum >/dev/null; then
  echo "${RUNNER_SHA256}  ${tarball}" | sha256sum --check
else
  echo "${RUNNER_SHA256}  ${tarball}" | shasum -a 256 --check
fi
tar xzf "$tarball"
rm -f "$tarball"

if [ "$(uname -s)" = "Linux" ]; then
  ./bin/installdependencies.sh
fi

chown -R "${RUNNER_USER}" "$runner_dir" /opt/hostedtoolcache
"$runner_dir/bin/Runner.Listener" --version
