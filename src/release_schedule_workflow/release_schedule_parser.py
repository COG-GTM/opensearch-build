# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

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

    def __init__(
        self,
        version: str,
        rc_date: str,
        release_date: str,
        release_issue: Optional[str],
        release_manager: List[str],
        release_manager_gh_handle: List[str],
    ) -> None:
        self.version = version
        self.rc_date = rc_date
        self.release_date = release_date
        self.release_issue = release_issue
        self.release_manager = release_manager
        self.release_manager_gh_handle = release_manager_gh_handle

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "rc_date": self.rc_date,
            "release_date": self.release_date,
            "release_issue": self.release_issue,
            "release_manager": self.release_manager,
            "release_manager_gh_handle": self.release_manager_gh_handle,
        }


class ReleaseScheduleParser:
    """Parses the release schedule table of https://opensearch.org/releases.html.

    A Python port of jenkins.ReleaseScheduleParser from opensearch-build-libraries, used by
    jenkins/release-workflows/release-schedule.jenkinsfile. The first table with class
    desktop-release-schedule-table has one header row followed by one row per release: release number,
    first RC date, latest possible release date, release manager(s) and tracking issue. Revised values keep
    the original struck through (<strike>, <s> or <del>); those are dropped so only the live value is used.
    Rows that cannot be parsed are skipped rather than failing the whole parse.
    """

    TABLE_CLASS = "desktop-release-schedule-table"
    STRUCK = ["strike", "s", "del"]
    DATE_PATTERN = re.compile(
        r"^(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,\s*(\d{4})$",
        re.IGNORECASE,
    )

    def __init__(self, html: str) -> None:
        self.html = html

    def parse_schedule(self) -> List[ReleaseSchedule]:
        soup = BeautifulSoup(self.html, "html.parser")
        table = soup.find("table", class_=self.TABLE_CLASS)
        if not isinstance(table, Tag):
            return []

        for struck in table.find_all(self.STRUCK):
            struck.decompose()

        schedules: List[ReleaseSchedule] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                # header row (<th>), or a layout row that carries no schedule data
                continue

            version = self.__text(cells[0])
            rc_date = self.normalize_date(self.__text(cells[1]))
            release_date = self.normalize_date(self.__text(cells[2]))
            if not version or rc_date is None or release_date is None:
                logging.warning(f"Skipping row {version!r}, could not parse {self.__text(cells[1])!r} / {self.__text(cells[2])!r} as dates.")
                continue

            anchor = cells[4].find("a", href=True)
            schedules.append(
                ReleaseSchedule(
                    version=version,
                    rc_date=rc_date,
                    release_date=release_date,
                    release_issue=str(anchor["href"]) if anchor else None,
                    release_manager=self.__managers(cells[3]),
                    release_manager_gh_handle=self.__handles(cells[3]),
                )
            )

        return schedules

    @classmethod
    def normalize_date(cls, display: str) -> Optional[str]:
        """Converts a display date like "January 27th, 2026" to "2026-01-27", or None."""
        match = cls.DATE_PATTERN.match(display)
        if match is None:
            return None
        month, day, year = match.group(1), match.group(2), match.group(3)
        return f"{year}-{MONTHS.index(month.lower()) + 1:02d}-{int(day):02d}"

    @staticmethod
    def __text(cell: Tag) -> str:
        return " ".join(cell.get_text(" ", strip=True).split())

    def __managers(self, cell: Tag) -> List[str]:
        anchors = cell.find_all("a")
        names = [self.__text(anchor) for anchor in anchors] if anchors else self.__text(cell).split(",")
        return [name.strip() for name in names if name.strip()]

    def __handles(self, cell: Tag) -> List[str]:
        """GitHub handles of the managers, aligned with __managers; empty unless every manager has one."""
        anchors = cell.find_all("a")
        handles = [handle for handle in (self.__profile_handle(str(anchor.get("href", ""))) for anchor in anchors) if handle]
        return handles if anchors and len(handles) == len(anchors) else []

    @staticmethod
    def __profile_handle(href: str) -> Optional[str]:
        url = urlparse(href.strip())
        if url.scheme not in ("http", "https") or url.netloc.lower() not in ("github.com", "www.github.com"):
            return None
        path = url.path.strip("/")
        if not path or "/" in path:
            return None
        return path[1:] if path.startswith("@") else path
