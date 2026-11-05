#!/usr/bin/env bash
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# `build job: <job>, parameters: [...], wait: true` across a trigger boundary (docs/jenkins-gha-inventory.md):
# dispatch <workflow> on <ref> with the JSON <inputs> through the workflow-dispatch API with return_run_details, so
# the response names the exact run it created, then wait for that run and fail unless it concludes with success.
# Needs GH_TOKEN with actions:write and GITHUB_REPOSITORY.
#
#   dispatch_and_watch.sh <workflow file> <ref> <inputs JSON>
set -euo pipefail

WORKFLOW="$1" REF="$2" INPUTS="$3"
POLL_SECONDS="${POLL_SECONDS:-60}"

echo "Dispatching ${WORKFLOW} on ${REF}: ${INPUTS}"
BODY=$(REF="${REF}" INPUTS="${INPUTS}" python3 -c 'import json, os
print(json.dumps({"ref": os.environ["REF"], "inputs": json.loads(os.environ["INPUTS"]), "return_run_details": True}))')
RESPONSE=$(printf '%s' "${BODY}" | gh api -X POST "repos/${GITHUB_REPOSITORY}/actions/workflows/${WORKFLOW}/dispatches" --input -)
RUN_ID=$(printf '%s' "${RESPONSE}" | python3 -c 'import json, sys
print(json.load(sys.stdin).get("workflow_run_id") or "")' 2>/dev/null || true)
if ! [[ "${RUN_ID}" =~ ^[0-9]+$ ]]; then
  echo "::error::The dispatch of ${WORKFLOW} did not return a workflow run id: ${RESPONSE}"
  exit 1
fi
URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}"
echo "::notice::Triggered ${WORKFLOW}: ${URL}"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "run-url=${URL}" >> "${GITHUB_OUTPUT}"
fi

LAST=""
while true; do
  STATE=$(gh run view "${RUN_ID}" --repo "${GITHUB_REPOSITORY}" --json status,conclusion --jq '.status + " " + .conclusion')
  if [ "${STATE}" != "${LAST}" ]; then
    echo "$(date -u +%H:%M:%S) ${WORKFLOW} run ${RUN_ID}: ${STATE}"
    LAST="${STATE}"
  fi
  case "${STATE}" in
    "completed success") echo "Build succeeded: ${URL}"; exit 0 ;;
    completed*) echo "::error::${WORKFLOW} run ${RUN_ID} finished with '${STATE#completed }': ${URL}"; exit 1 ;;
  esac
  sleep "${POLL_SECONDS}"
done
