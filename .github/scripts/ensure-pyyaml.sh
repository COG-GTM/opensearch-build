#!/bin/bash

# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Makes PyYAML importable for the helper scripts the composite actions call. The CI-runner images and
# GitHub-hosted Ubuntu runners already ship it; elsewhere it is installed for the current user only.
set -euo pipefail

if python3 -c 'import yaml' 2>/dev/null; then
  exit 0
fi
python3 -m pip install --quiet --user pyyaml 2>/dev/null \
  || python3 -m pip install --quiet --user --break-system-packages pyyaml
