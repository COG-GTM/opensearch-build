#!/bin/bash

# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Usage: index-records.sh <records.ndjson> <index> <alias> <mapping.json>
# Creates <index> with <mapping.json> and adds it to <alias> when it does not exist yet, then `_bulk`-indexes the
# records, as the curl block of publishDistributionBuildResults() / publishIntegTestResults() did. Needs
# METRICS_HOST_URL, REGION and the AWS session variables exported by aws-actions/configure-aws-credentials.
set -uo pipefail
set +x

RECORDS_FILE="$1"
INDEX="$2"
ALIAS="$3"
MAPPING_FILE="$4"

# SigV4 credentials go into a 0600 curl config file instead of the command line, so they never show up in the
# process table of a runner that also runs build and test code.
CURL_CONFIG=$(mktemp)
BULK_RESPONSE=$(mktemp)
chmod 600 "${CURL_CONFIG}"
trap 'rm -f "${CURL_CONFIG}" "${BULK_RESPONSE}"' EXIT
{
  echo "aws-sigv4 = \"aws:amz:${REGION}:es\""
  echo "user = \"${AWS_ACCESS_KEY_ID}:${AWS_SECRET_ACCESS_KEY}\""
  echo "header = \"x-amz-security-token:${AWS_SESSION_TOKEN}\""
} > "${CURL_CONFIG}"
SIGV4=(--config "${CURL_CONFIG}" --proto '=https')

INDEX_STATUS=$(curl -s -o /dev/null -w '%{http_code}' -I "${METRICS_HOST_URL}/${INDEX}" "${SIGV4[@]}")
if [ "${INDEX_STATUS}" = '200' ]; then
  echo "Index ${INDEX} already exists."
else
  echo "Index ${INDEX} does not exist. Creating..."
  CREATE_RESPONSE=$(curl -s -XPUT "${METRICS_HOST_URL}/${INDEX}" "${SIGV4[@]}" \
    -H 'Content-Type: application/json' --data-binary "@${MAPPING_FILE}")
  case "${CREATE_RESPONSE}" in
    *'"acknowledged":true'*)
      ALIAS_RESPONSE=$(curl -s -XPOST "${METRICS_HOST_URL}/_aliases" "${SIGV4[@]}" -H 'Content-Type: application/json' \
        -d "{\"actions\":[{\"add\":{\"index\":\"${INDEX}\",\"alias\":\"${ALIAS}\"}}]}")
      case "${ALIAS_RESPONSE}" in
        *'"acknowledged":true'*) echo "Index created and added to alias ${ALIAS}." ;;
        *) echo "::warning::Failed to add ${INDEX} to alias ${ALIAS}: ${ALIAS_RESPONSE}" ;;
      esac
      ;;
    *)
      echo "::error::Failed to create index ${INDEX}: ${CREATE_RESPONSE}"
      exit 1
      ;;
  esac
fi

if [ ! -s "${RECORDS_FILE}" ]; then
  echo "::warning::${RECORDS_FILE} is empty. No records to index into ${INDEX}."
  exit 0
fi
BULK_HTTP_CODE=$(curl -s -o "${BULK_RESPONSE}" -w '%{http_code}' -XPOST "${METRICS_HOST_URL}/${INDEX}/_bulk" \
  "${SIGV4[@]}" -H 'Content-Type: application/x-ndjson' --data-binary "@${RECORDS_FILE}")
echo "Bulk indexing into ${INDEX}: HTTP ${BULK_HTTP_CODE}"
# Jenkins only warned on a failed bulk call, so a metrics outage does not fail the build or test run.
if [ "${BULK_HTTP_CODE}" -lt 200 ] || [ "${BULK_HTTP_CODE}" -gt 299 ]; then
  echo "::warning::Bulk indexing into ${INDEX} failed with HTTP ${BULK_HTTP_CODE}"
elif grep -q '"errors":[[:space:]]*true' "${BULK_RESPONSE}"; then
  # _bulk answers 200 even when single documents were rejected, so the body decides.
  echo "::warning::Bulk indexing rejected at least one document: $(head -c 2000 "${BULK_RESPONSE}")"
fi
