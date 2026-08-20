#!/bin/bash

# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Create, update or close the [AUTOCUT] integration test failure issues described by report.json,
# the file written by fetch_integ_test_failures.py. This mirrors the createGithubIssue(issueEdit: true)
# and closeGithubIssue behaviour of the opensearch-build-libraries shared library, using gh.
#
# Usage: update_issues.sh <report.json>
# Requires: GH_TOKEN with issue write access on the component repositories.

set -euo pipefail

REPORT="${1:?Usage: update_issues.sh <report.json>}"
DAYS_TO_REOPEN="${DAYS_TO_REOPEN:-3}"
REOPEN_SINCE="$(date -d "${DAYS_TO_REOPEN} days ago" +'%Y-%m-%d')"

jq -c '.failed[]' "$REPORT" | while read -r entry; do
    repo=$(jq -r '.repository' <<< "$entry")
    title=$(jq -r '.title' <<< "$entry")
    body_file=$(jq -r '.body_file' <<< "$entry")
    labels=$(jq -r '.labels' <<< "$entry")

    open_issue=$(gh issue list --repo "$repo" -S "$title in:title is:open" --json number --jq '.[0].number')
    closed_issue=$(gh issue list --repo "$repo" -S "$title in:title is:closed closed:>=${REOPEN_SINCE}" --json number --jq '.[0].number')

    if [ -n "$open_issue" ]; then
        echo "Issue $open_issue already exists in $repo, editing the issue body"
        gh issue edit "$open_issue" --repo "$repo" --body-file "$body_file"
    elif [ -n "$closed_issue" ]; then
        echo "Re-opening recently closed issue $closed_issue in $repo"
        gh issue reopen "$closed_issue" --repo "$repo"
        gh issue edit "$closed_issue" --repo "$repo" --body-file "$body_file"
    else
        echo "Creating new issue in $repo"
        IFS=',' read -ra label_list <<< "$labels"
        for label in "${label_list[@]}"; do
            existing_label=$(gh label list --repo "$repo" -S "$label" --json name --jq '.[0].name')
            if [ "$existing_label" != "$label" ]; then
                echo "Creating missing label $label in $repo"
                gh label create "$label" --repo "$repo"
            fi
        done
        gh issue create --title "$title" --body-file "$body_file" --label "$labels" --label 'untriaged' --repo "$repo"
    fi
    sleep 3
done

jq -c '.passed[]' "$REPORT" | while read -r entry; do
    repo=$(jq -r '.repository' <<< "$entry")
    title=$(jq -r '.title' <<< "$entry")
    close_comment=$(jq -r '.close_comment' <<< "$entry")

    open_issue=$(gh issue list --repo "$repo" -S "$title in:title is:open" --json number --jq '.[0].number')
    if [ -n "$open_issue" ]; then
        echo "Integration tests passed, closing issue $open_issue in $repo"
        gh issue close "$open_issue" --repo "$repo" --comment "$close_comment"
    else
        echo "No open AUTOCUT integration test failure issue to close in $repo"
    fi
    sleep 3
done
