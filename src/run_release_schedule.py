#!/usr/bin/env python
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import argparse
import json
import logging
import sys

import requests

from release_schedule_workflow.release_schedule_parser import ReleaseScheduleParser
from system import console

DEFAULT_RELEASES_URL = "https://opensearch.org/releases.html"


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse the OpenSearch release schedule from the releases page.")
    parser.add_argument("--releases-url", default=DEFAULT_RELEASES_URL, help="URL of the OpenSearch releases schedule page to parse.")
    parser.add_argument("--output", help="Write the parsed schedule to this file as JSON, defaults to stdout only.")
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

    response = requests.get(args.releases_url, timeout=60)
    response.raise_for_status()

    schedules = ReleaseScheduleParser(response.text).parse_schedule()
    if not schedules:
        raise ValueError(f"No release schedules parsed from {args.releases_url}. The page layout may have changed.")

    logging.info(f"Parsed {len(schedules)} release schedule row(s) from {args.releases_url}.")
    for schedule in schedules:
        logging.info(f"Parsed schedule for {schedule.version} (RC: {schedule.rc_date}, Release: {schedule.release_date}).")

    content = json.dumps([schedule.to_dict() for schedule in schedules], indent=2)
    if args.output:
        with open(args.output, "w") as output_file:
            output_file.write(content)
        logging.info(f"Wrote {args.output}.")
    else:
        print(content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
