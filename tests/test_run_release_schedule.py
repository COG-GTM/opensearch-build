# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import json
import os
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from run_release_schedule import main

RELEASES_HTML = os.path.join(os.path.dirname(__file__), "tests_release_schedule_workflow", "data", "releases.html")


def mock_response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    return response


class TestRunReleaseSchedule(unittest.TestCase):

    @pytest.fixture(autouse=True)
    def _capfd(self, capfd: Any) -> None:
        self.capfd = capfd

    @patch("argparse._sys.argv", ["run_release_schedule.py", "--help"])
    def test_usage(self) -> None:
        with self.assertRaises(SystemExit):
            main()

        out, _ = self.capfd.readouterr()
        self.assertTrue(out.startswith("usage:"))

    @patch("argparse._sys.argv", ["run_release_schedule.py"])
    @patch("run_release_schedule.requests.get")
    def test_main_prints_schedule(self, requests_get: MagicMock) -> None:
        with open(RELEASES_HTML) as html:
            requests_get.return_value = mock_response(html.read())

        self.assertEqual(main(), 0)

        requests_get.assert_called_once_with("https://opensearch.org/releases.html", timeout=60)
        out, _ = self.capfd.readouterr()
        schedules = json.loads(out)
        self.assertEqual([schedule["version"] for schedule in schedules], ["3.5.0", "3.8.0"])
        self.assertEqual(schedules[0]["status"], "released")
        self.assertEqual(schedules[0]["registered_by"], "run_release_schedule.py")

    @patch.dict(os.environ, {"METRICS_HOST_URL": "https://metrics.example.com"})
    @patch("argparse._sys.argv", ["run_release_schedule.py", "--register", "--registered-by", "release-schedule #3"])
    @patch("run_release_schedule.metrics_auth")
    @patch("run_release_schedule.ReleaseScheduleRegister")
    @patch("run_release_schedule.requests.get")
    def test_main_registers_schedule(self, requests_get: MagicMock, register: MagicMock, metrics_auth: MagicMock) -> None:
        with open(RELEASES_HTML) as html:
            requests_get.return_value = mock_response(html.read())

        self.assertEqual(main(), 0)

        register.assert_called_once_with("https://metrics.example.com", metrics_auth.return_value)
        metrics_auth.assert_called_once_with("us-east-1")
        registered = register.return_value.register.call_args_list
        self.assertEqual([call.args[0] for call in registered], ["3.5.0", "3.8.0"])
        self.assertEqual(registered[1].args[1]["registered_by"], "release-schedule #3")

    @patch("run_release_schedule.requests.get")
    @patch("run_release_schedule.ReleaseScheduleRegister")
    def test_main_dry_run_does_not_register(self, register: MagicMock, requests_get: MagicMock) -> None:
        with open(RELEASES_HTML) as html:
            requests_get.return_value = mock_response(html.read())

        with patch("argparse._sys.argv", ["run_release_schedule.py"]):
            self.assertEqual(main(), 0)

        register.assert_not_called()

    @patch("run_release_schedule.requests.get")
    def test_main_writes_output_file(self, requests_get: MagicMock) -> None:
        with open(RELEASES_HTML) as html:
            requests_get.return_value = mock_response(html.read())

        with tempfile.TemporaryDirectory() as path:
            output = os.path.join(path, "release-schedule.json")
            with patch("argparse._sys.argv", ["run_release_schedule.py", "--output", output]):
                self.assertEqual(main(), 0)

            with open(output) as output_file:
                self.assertEqual(len(json.load(output_file)), 2)

    @patch("argparse._sys.argv", ["run_release_schedule.py"])
    @patch("run_release_schedule.requests.get")
    def test_main_errors_on_zero_rows(self, requests_get: MagicMock) -> None:
        requests_get.return_value = mock_response("<html><body>No schedule here.</body></html>")

        with self.assertRaises(ValueError) as context:
            main()

        self.assertIn("No release schedules parsed", str(context.exception))
