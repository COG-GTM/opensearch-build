#!/bin/bash
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Start a test-manifest CI-runner image the way Jenkins docker.image(...).inside(args) did, for steps to `docker exec`
# into: the workspace is mounted at the same path, systemd images (--entrypoint) keep their entrypoint as PID 1, and
# a `-u 1000` image gets the workspace chowned to 1000 first. Usage: start-ci-container.sh <name> <image> [args]
set -euo pipefail

NAME="$1"
IMAGE="$2"
read -r -a OPTIONS <<< "${3:-}"
COMMAND=(tail -f /dev/null)
for option in "${OPTIONS[@]}"; do
    case "${option}" in --entrypoint|--entrypoint=*) COMMAND=() ;; esac
    case "${option}" in 1000|1000:*) sudo chown -R 1000:1000 "${GITHUB_WORKSPACE}" ;; esac
done
# Anonymous public.ecr.aws pulls are rate limited (toomanyrequests), so back off and retry.
for attempt in 1 2 3 4 5; do
    docker pull "${IMAGE}" && break
    [ "${attempt}" = 5 ] && exit 1
    sleep $(( attempt * 30 ))
done
docker run -d --name "${NAME}" -v "${GITHUB_WORKSPACE}:${GITHUB_WORKSPACE}" -w "${GITHUB_WORKSPACE}" \
    "${OPTIONS[@]}" "${IMAGE}" "${COMMAND[@]}"
if [ "${#COMMAND[@]}" = 0 ]; then
    timeout 180 docker exec "${NAME}" systemctl is-system-running --wait || true
fi
