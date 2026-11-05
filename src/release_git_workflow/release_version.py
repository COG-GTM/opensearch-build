# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

CORE_COMPONENTS = ["OpenSearch", "OpenSearch-Dashboards", "functionalTestDashboards"]
PRODUCTS = ["opensearch", "opensearch-dashboards"]


def release_tag(component: str, version: str) -> str:
    """Tag createReleaseTag writes for component: the version for the core repositories, the four-part
    plugin version (3.10.0 -> 3.10.0.0, 3.0.0-alpha1 -> 3.0.0.0-alpha1) everywhere else."""
    if component in CORE_COMPONENTS:
        return version
    base, _, qualifier = version.partition("-")
    return f"{base}.0-{qualifier}" if qualifier else f"{base}.0"


def release_branch(version: str) -> str:
    """Release branch of a version: 3.10.0 -> 3.10."""
    return ".".join(version.split("-")[0].split(".")[0:2])
