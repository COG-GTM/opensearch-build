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

if [ ! -f "${MAPPING_FILE}" ]; then
  echo "::error::Mapping file ${MAPPING_FILE} not found."
  exit 1
fi

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

# Metrics never fail the build or test run (Jenkins only warned on a failed bulk call): every cluster-side failure below
# is a warning that the records were not published.
INDEX_STATUS=$(curl -s -o /dev/null -w '%{http_code}' -I "${METRICS_HOST_URL}/${INDEX}" "${SIGV4[@]}")
if [ "${INDEX_STATUS}" = '200' ]; then
  echo "Index ${INDEX} already exists."
else
  echo "Index ${INDEX} does not exist (HTTP ${INDEX_STATUS}). Creating..."
  CREATE_RESPONSE=$(curl -s -XPUT "${METRICS_HOST_URL}/${INDEX}" "${SIGV4[@]}" \
    -H 'Content-Type: application/json' --data-binary "@${MAPPING_FILE}")
  case "${CREATE_RESPONSE}" in
    *'"acknowledged":true'*) echo "Index ${INDEX} created." ;;
    *)
      echo "::warning::Failed to create index ${INDEX}, records not published: ${CREATE_RESPONSE}"
      exit 0
      ;;
  esac
fi

# Checked on every run, not only after creating the index, so an alias call that failed earlier is repaired.
ALIAS_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "${METRICS_HOST_URL}/${INDEX}/_alias/${ALIAS}" "${SIGV4[@]}")
if [ "${ALIAS_STATUS}" = '200' ]; then
  echo "Index ${INDEX} is in alias ${ALIAS}."
else
  ALIAS_RESPONSE=$(curl -s -XPOST "${METRICS_HOST_URL}/_aliases" "${SIGV4[@]}" -H 'Content-Type: application/json' \
    -d "{\"actions\":[{\"add\":{\"index\":\"${INDEX}\",\"alias\":\"${ALIAS}\"}}]}")
  case "${ALIAS_RESPONSE}" in
    *'"acknowledged":true'*) echo "Added ${INDEX} to alias ${ALIAS}." ;;
    *) echo "::warning::Failed to add ${INDEX} to alias ${ALIAS} (retried on the next run): ${ALIAS_RESPONSE}" ;;
  esac
fi

if [ ! -s "${RECORDS_FILE}" ]; then
  echo "::warning::${RECORDS_FILE} is empty. No records to index into ${INDEX}."
  exit 0
fi
BULK_HTTP_CODE=$(curl -s -o "${BULK_RESPONSE}" -w '%{http_code}' -XPOST "${METRICS_HOST_URL}/${INDEX}/_bulk" \
  "${SIGV4[@]}" -H 'Content-Type: application/x-ndjson' --data-binary "@${RECORDS_FILE}")
echo "Bulk indexing into ${INDEX}: HTTP ${BULK_HTTP_CODE}"
if [ "${BULK_HTTP_CODE}" -lt 200 ] || [ "${BULK_HTTP_CODE}" -gt 299 ]; then
  echo "::warning::Bulk indexing into ${INDEX} failed with HTTP ${BULK_HTTP_CODE}"
elif grep -q '"errors":[[:space:]]*true' "${BULK_RESPONSE}"; then
  # _bulk answers 200 even when single documents were rejected, so the body decides.
  echo "::warning::Bulk indexing rejected at least one document: $(head -c 2000 "${BULK_RESPONSE}")"
fi
