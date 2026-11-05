# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import importlib.util
import json
import os
import tempfile
import unittest
from typing import Any, Dict, List

import yaml

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
WORKFLOW = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "workflows", "release-promotion.yml")
SPEC = importlib.util.spec_from_file_location("release_promotion", os.path.join(SCRIPTS, "release_promotion.py"))
promotion: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promotion)


class TestPlan(unittest.TestCase):
    def test_both(self) -> None:
        result = promotion.plan(" 3.9.0 ", "12228 ", " 9054", "both", True)
        self.assertEqual(len(result["repos"]), 4)
        self.assertEqual(len(result["artifacts"]), 13)
        self.assertEqual(result["repos"][0], {
            "stage": "OpenSearch Debian Apt promotion", "job": "distribution-build-opensearch", "repo-type": "apt",
            "build": "12228", "manifest": "3.9.0/opensearch-3.9.0.yml"})
        self.assertNotIn(("distribution-build-opensearch", "linux", "tar", "x64"),
                         [(a["job"], a["platform"], a["distribution"], a["architecture"]) for a in result["artifacts"]])
        self.assertEqual(result["validation-projects"], "Both")
        self.assertEqual(result["staging-validation-options"],
                         "os-build-number=12228 osd-build-number=latest optional-args=using-staging-artifact-only platform=linux,windows")
        self.assertEqual(result["production-validation-options"],
                         "os-build-number=12228 osd-build-number=latest optional-args=validate-digest-only platform=linux,windows")
        self.assertEqual(result["source-images"], "opensearch:3.9.0.12228, opensearch-dashboards:3.9.0.9054")
        self.assertEqual(result["cloudfront-paths"], "/releases/bundle/opensearch/3.x/* /releases/bundle/opensearch-dashboards/3.x/*")
        self.assertTrue(result["opensearch"])

    def test_opensearch_only(self) -> None:
        result = promotion.plan("2.19.4", "11111", "", "opensearch", False)
        self.assertEqual({r["job"] for r in result["repos"] + result["artifacts"]}, {"distribution-build-opensearch"})
        self.assertEqual(len(result["artifacts"]), 6)
        self.assertEqual(result["validation-projects"], "opensearch")
        self.assertNotIn("osd-build-number", result["staging-validation-options"])
        self.assertEqual(result["source-images"], "opensearch:2.19.4.11111")
        self.assertFalse(result["tag-docker-latest"])
        self.assertEqual(result["cloudfront-paths"], "/releases/bundle/opensearch/2.x/* /releases/bundle/opensearch-dashboards/2.x/*")

    def test_dashboards_only(self) -> None:
        result = promotion.plan("3.9.0", "", "9054", "opensearch-dashboards", True)
        self.assertEqual({r["job"] for r in result["repos"] + result["artifacts"]}, {"distribution-build-opensearch-dashboards"})
        self.assertEqual(len(result["artifacts"]), 7)
        self.assertFalse(result["opensearch"])
        # Jenkins only narrows validation for PROJECTS == "opensearch"
        self.assertEqual(result["validation-projects"], "Both")
        self.assertEqual(result["source-images"], "opensearch-dashboards:3.9.0.9054")

    def test_parameter_errors(self) -> None:
        for args in [("", "1", "2", "both"), ("3.9.0", "", "2", "both"), ("3.9.0", "1", "", "both"),
                     ("3.9.0", "", "2", "opensearch"), ("3.9.0", "1", "", "opensearch-dashboards"), ("3.9.0", "1", "2", "Both")]:
            with self.subTest(args=args), self.assertRaises(promotion.ParameterError):
                promotion.plan(*args, True)

    def test_stages_match_workflow_job_names(self) -> None:
        with open(WORKFLOW, encoding="utf-8") as fh:
            names = {job.get("name") for job in yaml.safe_load(fh)["jobs"].values()}
        # matrix jobs are named after their Publish branch; the CloudFront/Docker parallel stage has no job of its own
        top = {stage[0] if isinstance(stage, tuple) else stage for stage in promotion.STAGES} - {"Invalidate cloudfront and promote docker"}
        self.assertEqual(top - names, set())
        self.assertLessEqual({"Invalidate Cloudfront", "Promote Docker", "${{ matrix.target.stage }}"}, names)
        self.assertIn(promotion.SUMMARY_JOB, names)

    def test_main_plan_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output, summary = os.path.join(tmp, "out"), os.path.join(tmp, "summary")
            code = promotion.main(["plan", "--release-version", "3.9.0", "--opensearch-rc", "12228", "--opensearch-dashboards-rc", "9054",
                                   "--github-output", output, "--summary", summary])
            self.assertEqual(code, 0)
            with open(output, encoding="utf-8") as fh:
                values = dict(line.rstrip("\n").split("=", 1) for line in fh)
            self.assertEqual(len(json.loads(values["artifacts"])), 13)
            self.assertEqual(values["opensearch"], "true")
            with open(summary, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("## Release promotion 3.9.0 (dry run)", text)
            self.assertIn("| Publish OpenSearch Linux tar x64 | distribution-promote-artifacts | distribution-build-opensearch linux tar x64 build 12228", text)
        self.assertEqual(promotion.main(["plan", "--release-version", "3.9.0", "--projects", "opensearch"]), 1)


def job(name: str, conclusion: str = "success") -> Dict[str, str]:
    return {"name": name, "conclusion": conclusion, "status": "completed", "html_url": f"https://example.test/{name.replace(' ', '_')}"}


class TestSummary(unittest.TestCase):
    JOBS: List[Dict[str, str]] = [
        job("Parameters Check"),
        job("Validate staging artifacts / verify-parameters"),
        job("OpenSearch Yum promotion / Plan"),
        job("OpenSearch Yum promotion / promote repo", "skipped"),
        job("Publish"),
        job("Promote Docker / Parameters Check"),
        job("Validate production artifacts / verify-parameters", "failure"),
        {"name": "Release promotion summary", "status": "in_progress", "conclusion": "", "html_url": ""},
    ]

    def test_lists_every_stage_in_order(self) -> None:
        text = promotion.summary(self.JOBS, "https://example.test/maven", "(not awaited)")
        lines = text.splitlines()
        self.assertEqual(lines[2], "- **Parameters Check** / [(this workflow)](https://example.test/Parameters_Check): `success`")
        self.assertIn("  - **OpenSearch Yum promotion** / [promote repo](https://example.test/OpenSearch_Yum_promotion_/_promote_repo): `skipped`", lines)
        self.assertIn("  - **Invalidate Cloudfront**: not run", lines)
        self.assertIn("- **Validate production artifacts** / [verify-parameters](https://example.test/Validate_production_artifacts_/_verify-parameters): `failure`", lines)
        self.assertEqual(lines[-1], "  - dispatched publish-to-maven run: https://example.test/maven (not awaited)")
        self.assertLess(text.index("Validate staging"), text.index("**Publish** (parallel)"))
        self.assertLess(text.index("Promote Docker"), text.index("Validate production"))
        self.assertNotIn("Release promotion summary", text)

    def test_caller_prefix(self) -> None:
        jobs = [{**j, "name": f"Release promotion / {j['name']}"} for j in self.JOBS]
        jobs.append(job("artifacts distribution-build-opensearch linux x64 tar / Plan"))
        jobs.append(job("docker opensearch:3.9.0.12228 / Parameters Check"))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "jobs.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([{"total_count": len(jobs), "jobs": jobs}], fh)
            out = os.path.join(tmp, "summary")
            self.assertEqual(promotion.main(["summary", "--jobs", path, "--summary", out]), 0)
            with open(out, encoding="utf-8") as fh:
                text = fh.read()
        self.assertEqual(text.count("**Parameters Check**"), 1)
        self.assertNotIn("artifacts distribution-build", text)
        self.assertIn("**Promote Docker** / [Parameters Check]", text)


if __name__ == "__main__":
    unittest.main()
