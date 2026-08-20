#!/bin/bash

# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.


###### Information ############################################################################
# Name:          rpm-dist-validation.sh
#
# About:         GitHub Actions replacement for the `rpmDistValidation(bundleManifestURL:)` call
#                in jenkins/legacy/rpm-validation.jenkinsfile. That function lives in the external
#                opensearch-build-libraries repository and cannot be called from Actions, so this
#                script reproduces the same contract with this repository's own tooling:
#                  1. download and parse the bundle manifest,
#                  2. derive version / architecture / build number from it and from its URL,
#                  3. hand off to ./validation.sh, which installs the staging RPM, starts the
#                     systemd service and runs the API test cases.
#
#                This must run inside a container started with systemd as PID 1
#                (see .github/workflows/rpm-validation.yml), because ./validation.sh calls
#                `systemctl start opensearch`.
#
# Usage:         ./scripts/validation/rpm-dist-validation.sh --bundle-manifest-url <url> [--arch x64|arm64]
###############################################################################################

set -e

BUNDLE_MANIFEST_URL=""
ARCH=""

function usage() {
    echo "Usage: $0 --bundle-manifest-url <url> [--arch x64|arm64]"
    echo ""
    echo "  --bundle-manifest-url   Staging bundle manifest url, e.g."
    echo "                          https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/2.0.0-rc1/2493/linux/x64/rpm/dist/opensearch/manifest.yml"
    echo "  --arch                  Expected architecture, x64 or arm64. Validated against the manifest."
}

while [ "$1" != "" ]; do
    case $1 in
        --bundle-manifest-url)
            shift
            BUNDLE_MANIFEST_URL=$1
            ;;
        --arch)
            shift
            ARCH=$1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
    shift
done

if [ -z "$BUNDLE_MANIFEST_URL" ]; then
    echo "ERROR: --bundle-manifest-url is required."
    usage
    exit 1
fi

DIR="$(cd "$(dirname "$0")/../.." && pwd)"
WORK_DIR="$(mktemp -d)"
MANIFEST_PATH="$WORK_DIR/manifest.yml"

echo "Downloading bundle manifest from $BUNDLE_MANIFEST_URL"
curl -sSL --fail "$BUNDLE_MANIFEST_URL" -o "$MANIFEST_PATH"
echo "Bundle manifest:"
cat "$MANIFEST_PATH"

# The bundle manifest build section is a flat mapping, see src/manifests/bundle_manifest.py.
function manifest_value() {
    grep -E "^  $1: " "$MANIFEST_PATH" | head -1 | sed -E "s/^  $1: *//" | tr -d '"' | tr -d "'"
}

VERSION=$(manifest_value version)
MANIFEST_ARCH=$(manifest_value architecture)
DISTRIBUTION=$(manifest_value distribution)
PLATFORM=$(manifest_value platform)

# https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/<version>/<build-number>/<platform>/<arch>/rpm/dist/opensearch/manifest.yml
BUILD_NUMBER=$(echo "$BUNDLE_MANIFEST_URL" | sed -nE 's#.*/distribution-build-opensearch/[^/]+/([0-9]+)/.*#\1#p')

if [ -z "$VERSION" ] || [ -z "$MANIFEST_ARCH" ]; then
    echo "ERROR: could not read version and architecture from the bundle manifest."
    exit 1
fi

if [ -z "$BUILD_NUMBER" ]; then
    echo "ERROR: could not derive the build number from $BUNDLE_MANIFEST_URL."
    echo "Expected .../distribution-build-opensearch/<version>/<build-number>/<platform>/<arch>/rpm/dist/opensearch/manifest.yml"
    exit 1
fi

if [ -n "$DISTRIBUTION" ] && [ "$DISTRIBUTION" != "rpm" ]; then
    echo "ERROR: bundle manifest distribution is '$DISTRIBUTION', this job only validates rpm."
    exit 1
fi

if [ -n "$ARCH" ] && [ "$ARCH" != "$MANIFEST_ARCH" ]; then
    echo "ERROR: requested architecture '$ARCH' does not match the bundle manifest architecture '$MANIFEST_ARCH'."
    exit 1
fi

echo "version=$VERSION build-number=$BUILD_NUMBER arch=$MANIFEST_ARCH platform=${PLATFORM:-linux}"

"$DIR/validation.sh" \
    --version "$VERSION" \
    --distribution rpm \
    --platform "${PLATFORM:-linux}" \
    --arch "$MANIFEST_ARCH" \
    --projects opensearch \
    --artifact-type staging \
    --os-build-number "$BUILD_NUMBER"
