#!/usr/bin/env bash
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# `build job: <job>, parameters: [...], wait: true` across a trigger boundary (docs/jenkins-gha-inventory.md):
# dispatch <workflow> on <ref> with the JSON <inputs>, find the run by its run-name (<title>), then wait for it and
# fail unless it concludes with success. Needs GH_TOKEN with actions:write and GITHUB_REPOSITORY.
#
#   dispatch_and_watch.sh <workflow file> <ref> <title> <inputs JSON>
set -euo pipefail

WORKFLOW="$1" REF="$2" TITLE="$3" INPUTS="$4"
POLL_SECONDS="${POLL_SECONDS:-60}"
export TITLE

START=$(date -u -d '-10 seconds' +%Y-%m-%dT%H:%M:%SZ)
echo "Dispatching ${WORKFLOW} on ${REF}: ${INPUTS}"
printf '%s' "${INPUTS}" | gh workflow run "${WORKFLOW}" --repo "${GITHUB_REPOSITORY}" --ref "${REF}" --json

RUN_ID=""
for _ in $(seq 1 30); do
  sleep 10
  RUN_ID=$(gh api -X GET "repos/${GITHUB_REPOSITORY}/actions/workflows/${WORKFLOW}/runs" \
    -f event=workflow_dispatch -f branch="${REF}" -f created=">=${START}" -f per_page=100 \
    --jq '[.workflow_runs[] | select(.display_title == env.TITLE and (.id | tostring) != env.GITHUB_RUN_ID)] | sort_by(.created_at) | .[0].id // empty')
  [ -n "${RUN_ID}" ] && break
done
if [ -z "${RUN_ID}" ]; then
  echo "::error::No '${TITLE}' run of ${WORKFLOW} appeared within 5 minutes of the dispatch."
  exit 1
fi
URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}"
echo "::notice::Triggered ${TITLE}: ${URL}"
[ -n "${GITHUB_OUTPUT:-}" ] && echo "run-url=${URL}" >> "${GITHUB_OUTPUT}"

LAST=""
while true; do
  STATE=$(gh run view "${RUN_ID}" --repo "${GITHUB_REPOSITORY}" --json status,conclusion --jq '.status + " " + .conclusion')
  if [ "${STATE}" != "${LAST}" ]; then
    echo "$(date -u +%H:%M:%S) ${TITLE}: ${STATE}"
    LAST="${STATE}"
  fi
  case "${STATE}" in
    "completed success") echo "Build succeeded: ${URL}"; exit 0 ;;
    completed*) echo "::error::${TITLE} finished with '${STATE#completed }': ${URL}"; exit 1 ;;
  esac
  sleep "${POLL_SECONDS}"
done
