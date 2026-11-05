#!/usr/bin/env bash
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Writes the owner and comma-separated repositories that a release ref plan writes to as step outputs,
# the scope of the GitHub App token that applies it.
set -euo pipefail

plan="$1"
owners=$(jq -r '.scope | keys | length' "$plan")
if [ "$owners" -gt 1 ]; then
  echo "::error::The plan writes to repositories of more than one owner: $(jq -r '.scope | keys | join(", ")' "$plan")"
  exit 1
fi
{
  echo "owner=$(jq -r '.scope | keys | first // ""' "$plan")"
  echo "repositories=$(jq -r '.scope | to_entries | first.value // [] | join(",")' "$plan")"
} >> "$GITHUB_OUTPUT"
