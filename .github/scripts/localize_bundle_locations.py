#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Point the bundle manifests of a build from this run at the local copy the integ-test job tests.

A build that was not uploaded (dry run) keeps the Jenkins https://.../<platform>/<architecture>/<distribution>/
locations in <root>/dist/*/manifest.yml; the test job's local copy is rewritten to <root>/(dist|builds)/... and
every rewritten location must exist, so a wrong mapping can never fall back to a remote download.
"""

import argparse
import glob
import os
import re
import sys
from typing import List, Optional

import yaml


def localize(root: str, platform: str, architecture: str, distribution: str) -> List[str]:
    """Rewrite the bundle manifests under root/dist in place; return the rewritten locations."""
    root = os.path.abspath(root)
    pattern = re.compile(rf"https?://\S+/{re.escape(platform)}/{re.escape(architecture)}/{re.escape(distribution)}/(dist|builds)/")
    rewritten: List[str] = []
    for manifest in sorted(glob.glob(os.path.join(root, "dist", "*", "manifest.yml"))):
        with open(manifest) as f:
            text = f.read()
        localized = pattern.sub(lambda match: f"{root}/{match.group(1)}/", text)
        if localized == text:
            continue
        with open(manifest, "w") as f:
            f.write(localized)
        bundle = yaml.safe_load(localized)
        locations = [bundle["build"]["location"]] + [c["location"] for c in bundle.get("components") or [] if "location" in c]
        rewritten += [location for location in locations if location.startswith(f"{root}/")]
    return rewritten


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", help="Local <distribution>/ tree with dist/ and builds/")
    parser.add_argument("--platform", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--distribution", required=True)
    args = parser.parse_args(argv)

    rewritten = localize(args.root, args.platform, args.architecture, args.distribution)
    missing = [location for location in rewritten if not os.path.exists(location)]
    for location in rewritten:
        print(f"{'missing' if location in missing else 'local'}: {location}")
    if missing:
        print(f"::error::{len(missing)} rewritten bundle location(s) are not in {args.root}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
