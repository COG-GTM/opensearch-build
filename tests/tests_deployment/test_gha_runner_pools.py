# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import os
import re
import unittest
from typing import Dict, List

import yaml

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
ADR = os.path.join(ROOT, "docs", "adr", "0001-gha-runners-and-secrets.md")
POOLS = os.path.join(ROOT, "deployment", "gha-runners", "runner-pools.yaml")
ACTIONLINT = os.path.join(ROOT, ".github", "actionlint.yaml")
ROW = re.compile(r"^\| `(?P<jenkins>[^`]+)` \| [^|]+ \| `\[(?P<labels>[^\]]+)\]` \| (?P<instance>[a-z0-9]+\.[a-z0-9]+)")


def adr_label_map() -> Dict[str, Dict[str, object]]:
    rows: Dict[str, Dict[str, object]] = {}
    with open(ADR) as f:
        for line in f:
            match = ROW.match(line)
            if match:
                rows[match["jenkins"]] = {
                    "labels": [label.strip() for label in match["labels"].split(",")],
                    "instance_type": match["instance"],
                }
    return rows


class TestGhaRunnerPools(unittest.TestCase):
    def setUp(self) -> None:
        with open(POOLS) as f:
            self.pools: Dict[str, Dict] = yaml.safe_load(f)
        self.adr = adr_label_map()

    def test_adr_table_parsed(self) -> None:
        self.assertEqual(len(self.adr), 11)

    def test_one_pool_per_adr_row(self) -> None:
        self.assertEqual(sorted(p["jenkins_label"] for p in self.pools.values()), sorted(self.adr))

    def test_labels_and_instance_types_match_adr(self) -> None:
        for key, pool in self.pools.items():
            with self.subTest(pool=key):
                row = self.adr[pool["jenkins_label"]]
                self.assertEqual(pool["labels"], row["labels"])
                self.assertEqual(pool["instance_type"], row["instance_type"])

    def test_pool_key_is_unique_last_label(self) -> None:
        last_labels: List[str] = [pool["labels"][-1] for pool in self.pools.values()]
        self.assertEqual(len(last_labels), len(set(last_labels)))
        for key, pool in self.pools.items():
            with self.subTest(pool=key):
                self.assertEqual(pool["labels"][-1], key)
                os_label = {"osx": "macos"}.get(pool["os"], pool["os"])
                self.assertEqual(pool["labels"][:3], ["self-hosted", os_label, pool["arch"]])

    def test_retired_labels_have_no_pool(self) -> None:
        jenkins_labels = [p["jenkins_label"] for p in self.pools.values()]
        for retired in ["Ubuntu2404-X64-M58xlarge-Single-Host", "Windows2019-X64-C524xlarge-Single-Host"]:
            self.assertNotIn(retired, jenkins_labels)

    def test_pool_ids_fit_aws_name_limits(self) -> None:
        ids = [pool["id"] for pool in self.pools.values()]
        self.assertEqual(len(ids), len(set(ids)))
        for pool_id in ids:
            with self.subTest(id=pool_id):
                # "<prefix up to 8>-<id>-ssm-housekeeper" must stay within the 64-char EventBridge rule limit.
                self.assertLessEqual(len(pool_id), 24)
                self.assertRegex(pool_id, r"^[a-z0-9-]+$")

    def test_actionlint_knows_pool_labels(self) -> None:
        with open(ACTIONLINT) as f:
            known = yaml.safe_load(f)["self-hosted-runner"]["labels"]
        for key in self.pools:
            self.assertIn(key, known)
