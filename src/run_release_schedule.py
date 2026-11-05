#!/usr/bin/env python
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import argparse
import datetime
import json
import logging
import os
import sys
from urllib.parse import urlparse

import requests

from release_schedule_workflow.release_schedule_parser import ReleaseScheduleParser
from release_schedule_workflow.release_schedule_register import ReleaseScheduleRegister, schedule_document
from system import console

DEFAULT_RELEASES_URL = "https://opensearch.org/releases.html"
RELEASES_HOSTS = ["opensearch.org", "www.opensearch.org"]


def metrics_auth(region: str) -> object:
    import boto3
    from requests_aws4auth import AWS4Auth

    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    return AWS4Auth(credentials.access_key, credentials.secret_key, region, "es", session_token=credentials.token)


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse the OpenSearch release schedule and optionally register it in the metrics cluster.")
    parser.add_argument("--releases-url", default=DEFAULT_RELEASES_URL, help="URL of the OpenSearch releases schedule page to parse.")
    parser.add_argument("--from-json", help="Use the documents of an earlier run's --output file instead of parsing the page again.")
    parser.add_argument("--output", help="Write the schedule documents to this file as JSON, defaults to stdout only.")
    parser.add_argument("--register", action="store_true", help="Upsert each row into the opensearch_release_schedule index (needs METRICS_HOST_URL and AWS credentials).")
    parser.add_argument("--registered-by", default=os.environ.get("REGISTERED_BY", "run_release_schedule.py"), help="Value of registered_by on each document.")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"), help="AWS region of the metrics cluster, for SigV4.")
    parser.add_argument(
        "-v",
        "--verbose",
        help="Show more verbose output.",
        action="store_const",
        default=logging.INFO,
        const=logging.DEBUG,
        dest="logging_level",
    )
    args = parser.parse_args()
    console.configure(level=args.logging_level)

    if args.from_json:
        with open(args.from_json) as input_file:
            documents = json.load(input_file)
        logging.info(f"Loaded {len(documents)} release schedule document(s) from {args.from_json}.")
    else:
        url = urlparse(args.releases_url)
        if url.scheme != "https" or url.hostname not in RELEASES_HOSTS:
            raise ValueError(f"--releases-url must be an https URL on {', '.join(RELEASES_HOSTS)}: {args.releases_url}")
        response = requests.get(args.releases_url, timeout=60)
        response.raise_for_status()

        schedules = ReleaseScheduleParser(response.text).parse_schedule()
        if not schedules:
            raise ValueError(f"No release schedules parsed from {args.releases_url}. The page layout may have changed.")
        logging.info(f"Parsed {len(schedules)} release schedule row(s) from {args.releases_url}.")

        now = datetime.datetime.now(datetime.timezone.utc)
        documents = [schedule_document(schedule, args.registered_by, now) for schedule in schedules]

    if args.register:
        register = ReleaseScheduleRegister(os.environ["METRICS_HOST_URL"], metrics_auth(args.region))
        for document in documents:
            register.register(str(document["version"]), document)
    else:
        for document in documents:
            logging.info(f"Dry run: would register {document['version']} (RC: {document['rc_date']}, Release: {document['release_date']}, status: {document['status']}).")

    content = json.dumps(documents, indent=2)
    if args.output:
        with open(args.output, "w") as output_file:
            output_file.write(content)
        logging.info(f"Wrote {args.output}.")
    else:
        print(content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
