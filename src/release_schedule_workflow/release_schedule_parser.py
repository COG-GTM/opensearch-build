# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


class ReleaseSchedule:
    """A single row of the release schedule table on the OpenSearch releases page."""

    def __init__(self, version: str, rc_date: str, release_date: str, release_issue: str, release_manager: str) -> None:
        self.version = version
        self.rc_date = rc_date
        self.release_date = release_date
        self.release_issue = release_issue
        self.release_manager = release_manager

    def to_dict(self) -> Dict[str, str]:
        return {
            "version": self.version,
            "rc_date": self.rc_date,
            "release_date": self.release_date,
            "release_issue": self.release_issue,
            "release_manager": self.release_manager,
        }


class ReleaseScheduleParser:
    """Parses the release schedule table of https://opensearch.org/releases.html.

    A Python port of lib.jenkins.ReleaseScheduleParser from opensearch-build-libraries, used by
    jenkins/release-workflows/release-schedule.jenkinsfile. The table has one header row followed by
    one row per release: release number, first RC date, latest possible release date, release manager
    and tracking issue. Superseded dates are struck through (<s>) and are ignored in favour of the
    date that follows them.
    """

    TABLE_CLASS = "desktop-release-schedule-table"
    DATE_PATTERN = re.compile(
        r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
        re.IGNORECASE,
    )

    def __init__(self, html: str) -> None:
        self.html = html

    def parse_schedule(self) -> List[ReleaseSchedule]:
        soup = BeautifulSoup(self.html, "html.parser")
        schedules: List[ReleaseSchedule] = []

        for table in soup.find_all("table", class_=self.TABLE_CLASS):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    # header row, or a layout row that carries no schedule data
                    continue

                version = self.__text(cells[0])
                if not re.match(r"^\d+\.\d+\.\d+$", version):
                    continue

                rc_date = self.__date(cells[1])
                release_date = self.__date(cells[2])
                if rc_date is None or release_date is None:
                    continue

                release_manager = self.__text(cells[3]) if len(cells) > 3 else ""
                release_issue = self.__text(cells[4]) if len(cells) > 4 else ""

                schedules.append(
                    ReleaseSchedule(
                        version=version,
                        rc_date=rc_date,
                        release_date=release_date,
                        release_issue=release_issue,
                        release_manager=release_manager,
                    )
                )

        return schedules

    def __text(self, cell: Any) -> str:
        return " ".join(cell.get_text(" ", strip=True).split())

    def __date(self, cell: Tag) -> Optional[str]:
        # Drop struck-through dates, they have been superseded by the date next to them.
        for struck in cell.find_all("s"):
            struck.decompose()

        match = self.DATE_PATTERN.search(self.__text(cell))
        if match is None:
            return None

        month, day, year = match.group(1), match.group(2), match.group(3)
        return datetime.strptime(f"{month.lower()} {day} {year}", "%B %d %Y").strftime("%Y-%m-%d")
