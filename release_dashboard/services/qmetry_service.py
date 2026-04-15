"""
QMetry Service — boolean test-coverage flags per Jira ticket.

Design note
-----------
This service deliberately returns *only* boolean flags (test_case_exists,
executed, in_regression). No complex mapping is performed here; that
responsibility belongs to the Governance Engine.

QMetry API used
---------------
  POST /rest/api/2/testcase/search   — search test cases by label / summary
  GET  /rest/api/2/testcycle/{id}/testcase — list test cases in a cycle

Both endpoints belong to QMetry Test Management for Jira (QTM4J).
Adjust base_url and paths if you use the standalone QMetry platform.
"""

import logging
from typing import List

import requests

from models import Feature

logger = logging.getLogger(__name__)


class QMetryService:
    def __init__(self, base_url: str, token: str) -> None:
        """
        Args:
            base_url: QMetry instance base URL.
                      QTM4J example: ``https://qtm4j.qmetry.com``
            token:    QMetry API token (passed as ``Authorization`` header value).
        """
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Public — primary boolean checks
    # ------------------------------------------------------------------

    def check_test_case_exists(self, jira_id: str) -> bool:
        """Return True if at least one test case exists whose summary/label
        contains *jira_id*.

        Searches QMetry for test cases labelled with the Jira ticket key.
        """
        url = f"{self.base_url}/rest/api/2/testcase/search"
        payload = {
            "maxResults": 1,
            "startAt": 0,
            "query": jira_id,          # free-text search across summary & labels
        }
        try:
            response = self._session.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            total = data.get("total", data.get("totalCount", len(data.get("data", []))))
            return int(total) > 0
        except requests.RequestException as exc:
            logger.warning("QMetry test-case search failed for %s: %s", jira_id, exc)
            return False

    def check_test_executed(self, jira_id: str, cycle_id: str) -> bool:
        """Return True if a test case linked to *jira_id* was executed
        (status is not 'NOT_RUN') within the given *cycle_id*.
        """
        url = f"{self.base_url}/rest/api/2/testcycle/{cycle_id}/testcase"
        try:
            response = self._session.get(url, params={"maxResults": 200})
            response.raise_for_status()
            cases = response.json().get("data", [])
            for case in cases:
                if self._case_matches_jira_id(case, jira_id):
                    status = (case.get("executionStatus") or case.get("status") or "").upper()
                    return status not in ("", "NOT_RUN", "NOTRUN", "UNEXECUTED")
            return False
        except requests.RequestException as exc:
            logger.warning(
                "QMetry cycle execution check failed (cycle=%s, jira=%s): %s",
                cycle_id, jira_id, exc,
            )
            return False

    def check_in_regression(self, jira_id: str, regression_cycle_id: str) -> bool:
        """Return True if a test case linked to *jira_id* is present in the
        designated regression test cycle.
        """
        url = f"{self.base_url}/rest/api/2/testcycle/{regression_cycle_id}/testcase"
        try:
            response = self._session.get(url, params={"maxResults": 500})
            response.raise_for_status()
            cases = response.json().get("data", [])
            return any(self._case_matches_jira_id(c, jira_id) for c in cases)
        except requests.RequestException as exc:
            logger.warning(
                "QMetry regression check failed (cycle=%s, jira=%s): %s",
                regression_cycle_id, jira_id, exc,
            )
            return False

    # ------------------------------------------------------------------
    # Bulk feature enrichment
    # ------------------------------------------------------------------

    def enrich_feature(
        self,
        feature: Feature,
        cycle_id: str,
        regression_cycle_id: str = "",
    ) -> Feature:
        """Populate the three QMetry boolean flags on a :class:`Feature` in-place."""
        feature.test_case_exists = self.check_test_case_exists(feature.jira_id)
        feature.executed = self.check_test_executed(feature.jira_id, cycle_id) if cycle_id else False
        feature.in_regression = (
            self.check_in_regression(feature.jira_id, regression_cycle_id)
            if regression_cycle_id
            else False
        )
        return feature

    def enrich_features(
        self,
        features: List[Feature],
        cycle_id: str,
        regression_cycle_id: str = "",
    ) -> List[Feature]:
        """Bulk-enrich a list of :class:`Feature` objects with QMetry flags."""
        return [self.enrich_feature(f, cycle_id, regression_cycle_id) for f in features]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _case_matches_jira_id(case: dict, jira_id: str) -> bool:
        """Return True if the test case summary, name, or labels contain *jira_id*."""
        targets = [
            case.get("summary", ""),
            case.get("name", ""),
            case.get("testCaseName", ""),
            " ".join(case.get("labels", [])),
        ]
        return any(jira_id.upper() in t.upper() for t in targets if t)
