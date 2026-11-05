#!/bin/bash
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Dry run of an s3-sync upload: print the s3://<bucket>/<key> every local file would be written to, the layout
# uploadTestResults() / createUploadTestReportManifest() used in Jenkins.
# Usage: print-s3-keys.sh <bucket> <remote-path> <local file or directory>
set -euo pipefail

BUCKET="$1"
REMOTE="${2%/}"
LOCAL="$3"

if [ -f "${LOCAL}" ]; then
    echo "[dry run] would upload ${LOCAL} to s3://${BUCKET}/${REMOTE}"
    echo "- \`s3://${BUCKET}/${REMOTE}\`" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
elif [ -d "${LOCAL}" ]; then
    COUNT=0
    while IFS= read -r -d '' file; do
        echo "[dry run] would upload ${file} to s3://${BUCKET}/${REMOTE}/${file#"${LOCAL}"/}"
        COUNT=$((COUNT + 1))
    done < <(find "${LOCAL}" -type f -print0 | sort -z)
    echo "[dry run] ${COUNT} file(s) under s3://${BUCKET}/${REMOTE}/"
    echo "- ${COUNT} file(s) under \`s3://${BUCKET}/${REMOTE}/\`" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
else
    echo "[dry run] nothing to upload: ${LOCAL} does not exist (s3://${BUCKET}/${REMOTE})"
fi
