# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import datetime
import unittest
from unittest.mock import MagicMock, patch

from release_schedule_workflow.release_schedule_parser import ReleaseSchedule
from release_schedule_workflow.release_schedule_register import ReleaseScheduleRegister, derive_status, schedule_document, schedule_document_id

SCHEDULE = ReleaseSchedule("3.10.0", "2026-11-03", "2026-11-17", "https://github.com/opensearch-project/opensearch-build/issues/6484", ["Divya Madala"], ["Divyaasm"])


class TestReleaseScheduleRegister(unittest.TestCase):

    def test_derive_status(self) -> None:
        today = datetime.date(2026, 11, 5)
        self.assertEqual("released", derive_status("2026-10-01", "2026-11-04", today))
        self.assertEqual("active", derive_status("2026-11-03", "2026-11-17", today))
        self.assertEqual("active", derive_status("2026-12-05", "2026-12-15", today))
        self.assertEqual("inactive", derive_status("2026-12-06", "2026-12-15", today))
        self.assertEqual("inactive", derive_status(None, "2026-12-15", today))
        self.assertEqual("inactive", derive_status("not-a-date", None, today))

    def test_schedule_document_id_matches_java_name_uuid(self) -> None:
        # java.util.UUID.nameUUIDFromBytes("release-schedule-3.10.0".getBytes("UTF-8"))
        self.assertEqual("5ad683c4-52b8-3aa0-88dc-22cb4a77c8ab", schedule_document_id("3.10.0"))
        self.assertNotEqual(schedule_document_id("3.10.0"), schedule_document_id("3.11.0"))

    def test_schedule_document(self) -> None:
        document = schedule_document(SCHEDULE, "release-schedule #7", datetime.datetime(2026, 11, 5, 15, 0, 0))
        self.assertEqual("active", document["status"])
        self.assertEqual("2026-11-05T15:00:00Z", document["registered_at"])
        self.assertEqual("release-schedule #7", document["registered_by"])
        self.assertEqual(["Divyaasm"], document["release_manager_gh_handle"])

    @patch("release_schedule_workflow.release_schedule_register.requests.put")
    def test_register_puts_document(self, put: MagicMock) -> None:
        put.return_value = MagicMock(status_code=201)
        auth = MagicMock()
        ReleaseScheduleRegister("https://metrics.example.com/", auth).register("3.10.0", {"status": "active"})
        put.assert_called_once_with(
            f"https://metrics.example.com/opensearch_release_schedule/_doc/{schedule_document_id('3.10.0')}", json={"status": "active"}, auth=auth, timeout=60
        )

    @patch("release_schedule_workflow.release_schedule_register.requests.put")
    def test_register_fails_on_error_status(self, put: MagicMock) -> None:
        put.return_value = MagicMock(status_code=403)
        with self.assertRaisesRegex(RuntimeError, "HTTP status: 403"):
            ReleaseScheduleRegister("https://metrics.example.com", None).register("3.10.0", {"status": "active"})
