# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import os
import unittest

from release_schedule_workflow.release_schedule_parser import ReleaseScheduleParser


class TestReleaseScheduleParser(unittest.TestCase):
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

    def __html(self, name: str = "releases.html") -> str:
        with open(os.path.join(self.DATA_DIR, name)) as f:
            return f.read()

    def test_parses_every_schedule_row(self) -> None:
        schedules = ReleaseScheduleParser(self.__html()).parse_schedule()
        self.assertEqual(2, len(schedules))
        self.assertEqual(["3.5.0", "3.8.0"], [schedule.version for schedule in schedules])

    def test_parses_dates_as_iso(self) -> None:
        schedules = ReleaseScheduleParser(self.__html()).parse_schedule()
        self.assertEqual("2026-01-27", schedules[0].rc_date)
        self.assertEqual("2026-02-10", schedules[0].release_date)

    def test_ignores_superseded_rc_date(self) -> None:
        schedules = ReleaseScheduleParser(self.__html()).parse_schedule()
        self.assertEqual("2026-07-21", schedules[1].rc_date)
        self.assertEqual("2026-08-04", schedules[1].release_date)

    def test_parses_release_manager_and_issue(self) -> None:
        schedules = ReleaseScheduleParser(self.__html()).parse_schedule()
        self.assertEqual("Foo", schedules[0].release_manager)
        self.assertEqual("5897", schedules[0].release_issue)

    def test_to_dict(self) -> None:
        schedules = ReleaseScheduleParser(self.__html()).parse_schedule()
        self.assertEqual(
            {
                "version": "3.8.0",
                "rc_date": "2026-07-21",
                "release_date": "2026-08-04",
                "release_issue": "6278",
                "release_manager": "Bar",
            },
            schedules[1].to_dict(),
        )

    def test_ignores_other_tables_and_non_schedule_rows(self) -> None:
        schedules = ReleaseScheduleParser(self.__html()).parse_schedule()
        self.assertNotIn("Dates are subject to change.", [schedule.version for schedule in schedules])

    def test_empty_when_table_missing(self) -> None:
        self.assertEqual([], ReleaseScheduleParser("<html><body><p>no schedule here</p></body></html>").parse_schedule())
