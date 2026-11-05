# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import datetime
import hashlib
import logging
import uuid
from typing import Any, Dict, Optional

import requests

from release_schedule_workflow.release_schedule_parser import ReleaseSchedule

SCHEDULE_INDEX = "opensearch_release_schedule"
ACTIVATION_WINDOW_DAYS = 30


def derive_status(rc_date: Optional[str], release_date: Optional[str], today: datetime.date) -> str:
    """Port of jenkins.ReleaseSchedule.deriveStatus: released after the release date, active within
    ACTIVATION_WINDOW_DAYS of the RC date, inactive otherwise (or when the RC date is unknown)."""

    def parse(value: Optional[str]) -> Optional[datetime.date]:
        try:
            return datetime.date.fromisoformat(value) if value else None
        except ValueError:
            return None

    release, rc = parse(release_date), parse(rc_date)
    if release is not None and today > release:
        return "released"
    if rc is None:
        return "inactive"
    return "active" if (rc - today).days <= ACTIVATION_WINDOW_DAYS else "inactive"


def schedule_document_id(version: str) -> str:
    """Same id as Java's UUID.nameUUIDFromBytes("release-schedule-<version>"), so the Jenkins job and this
    workflow upsert the same document."""
    return str(uuid.UUID(bytes=hashlib.md5(f"release-schedule-{version}".encode("utf-8")).digest(), version=3))


def schedule_document(schedule: ReleaseSchedule, registered_by: str, now: datetime.datetime) -> Dict[str, Any]:
    document = schedule.to_dict()
    document.update(
        {
            "status": derive_status(schedule.rc_date, schedule.release_date, now.date()),
            "registered_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "registered_by": registered_by,
        }
    )
    return document


class ReleaseScheduleRegister:
    """Upserts schedule documents into the opensearch_release_schedule index of the metrics cluster."""

    def __init__(self, metrics_url: str, auth: Any) -> None:
        self.metrics_url = metrics_url.rstrip("/")
        self.auth = auth

    def register(self, version: str, document: Dict[str, Any]) -> None:
        url = f"{self.metrics_url}/{SCHEDULE_INDEX}/_doc/{schedule_document_id(version)}"
        response = requests.put(url, json=document, auth=self.auth, timeout=60)
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Failed to index the {version} schedule into {SCHEDULE_INDEX}. HTTP status: {response.status_code}")
        logging.info(f"Registered release schedule for {version} with status {document['status']!r}.")
