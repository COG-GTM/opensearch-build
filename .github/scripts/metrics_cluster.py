# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Search client for the OpenSearch metrics cluster, shared by the Autocut query scripts.

Port of opensearch-build-libraries (jenkins@12.0.0) src/utils/OpenSearchMetricsQuery.groovy, which runs
`curl <METRICS_HOST_URL>/<index>/_search --aws-sigv4 aws:amz:us-east-1:es` with the OpenSearchJenkinsAccessRole session.
Here the session comes from aws-actions/configure-aws-credentials (AWS_ROLE_METRICS) and the endpoint from the
METRICS_CLUSTER_ENDPOINT secret.

Dry runs without those secrets read the same indices anonymously through the public metrics.opensearch.org Dashboards
console proxy, so a pull request run prints what the live run would do against today's data.
"""

import os
import sys
from typing import Any, Callable, Dict, Optional

import requests

SIGV4_REGION = "us-east-1"
SIGV4_SERVICE = "es"
PUBLIC_DASHBOARDS_URL = "https://metrics.opensearch.org/_dashboards"
TIMEOUT_SECONDS = 60

Search = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class MetricsCluster:
    def __init__(self, endpoint: str = "", dashboards_url: str = "", session: Optional[requests.Session] = None) -> None:
        if bool(endpoint) == bool(dashboards_url):
            raise ValueError("Pass exactly one of endpoint (signed cluster access) or dashboards_url (public read-only proxy)")
        self.endpoint = endpoint.rstrip("/")
        self.dashboards_url = dashboards_url.rstrip("/")
        self.session = session or requests.Session()
        self.auth: Any = None
        if self.endpoint:
            from requests_aws4auth import AWS4Auth

            access_key = os.environ.get("AWS_ACCESS_KEY_ID")
            secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
            if not access_key or not secret_key:
                raise RuntimeError("AWS credentials for the metrics cluster are not set; run configure-aws-credentials with AWS_ROLE_METRICS first")
            self.auth = AWS4Auth(access_key, secret_key, SIGV4_REGION, SIGV4_SERVICE, session_token=os.environ.get("AWS_SESSION_TOKEN"))

    @property
    def source(self) -> str:
        return "metrics cluster (SigV4)" if self.endpoint else f"public Dashboards proxy {self.dashboards_url}"

    def search(self, index: str, query: Dict[str, Any]) -> Dict[str, Any]:
        if self.endpoint:
            response = self.session.get(f"{self.endpoint}/{index}/_search", auth=self.auth, json=query, timeout=TIMEOUT_SECONDS)
        else:
            response = self.session.post(
                f"{self.dashboards_url}/api/console/proxy",
                params={"path": f"{index}/_search", "method": "GET"},
                headers={"osd-xsrf": "true"},
                json=query,
                timeout=TIMEOUT_SECONDS,
            )
        response.raise_for_status()
        return dict(response.json())


def from_args(endpoint: str, dashboards_url: str) -> MetricsCluster:
    """An explicit dashboards_url wins over the endpoint so dry runs never need AWS credentials."""
    if dashboards_url or not endpoint:
        cluster = MetricsCluster(dashboards_url=dashboards_url or PUBLIC_DASHBOARDS_URL)
    else:
        cluster = MetricsCluster(endpoint=endpoint)
    print(f"Reading metrics from the {cluster.source}", file=sys.stderr)
    return cluster


def write_output(name: str, value: str) -> None:
    """Append a (possibly multi-line) step output."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    delimiter = f"EOF_{name.upper().replace('-', '_')}_{os.getpid()}"
    with open(path, "a") as output:
        output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as summary:
            summary.write(text + "\n")
