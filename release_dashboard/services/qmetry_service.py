"""
QMetry Service — test execution counts per Jira ticket.

Each feature is enriched with a :class:`TestExecution` object carrying
pass / fail / blocked / not_run counts, in_regression flag, and a computed
pass_rate.  Boolean convenience properties (test_case_exists, executed) are
derived from those counts inside the TestExecution dataclass.

QMetry API used (QTM4J — Test Management for Jira)
───────────────────────────────────────────────────
  POST /rest/api/2/testcase/search          — search by summary / label
  GET  /rest/api/2/testcycle/{id}/testcase  — execution list for a cycle

Adjust base_url / paths for standalone QMetry if needed.
"""

import logging
from typing import List, Optional

import requests

from models import Feature, TestExecution

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
    # Public — full execution summary
    # ------------------------------------------------------------------

    def get_test_execution_summary(
        self,
        jira_id: str,
        cycle_id: str,
        regression_cycle_id: str = "",
    ) -> TestExecution:
        """Return a :class:`TestExecution` with pass/fail/blocked/not_run counts.

        Steps
        -----
        1. Search for test cases matching *jira_id* (existence check).
        2. Walk the given *cycle_id* to tally execution statuses.
        3. Optionally check *regression_cycle_id* for the ``in_regression`` flag.
        """
        # ── 1. Existence check ──────────────────────────────────────────
        exists = self._test_case_exists(jira_id)
        if not exists:
            return TestExecution()   # all zeros / False

        # ── 2. Execution counts from the release cycle ──────────────────
        passed = failed = blocked = not_run = 0
        if cycle_id:
            cases = self._fetch_cycle_cases(cycle_id)
            for case in cases:
                if not self._case_matches_jira_id(case, jira_id):
                    continue
                raw_status = (
                    case.get("executionStatus")
                    or case.get("status")
                    or "NOT_RUN"
                ).upper().replace(" ", "_")

                if raw_status in ("PASS", "PASSED"):
                    passed += 1
                elif raw_status in ("FAIL", "FAILED"):
                    failed += 1
                elif raw_status in ("BLOCKED", "BLOCK"):
                    blocked += 1
                else:
                    not_run += 1

        # If the test case exists but nothing was found in the cycle,
        # record it as not_run so the counts add up to at least 1.
        total = passed + failed + blocked + not_run
        if total == 0:
            not_run = 1
            total = 1

        # ── 3. Regression membership ────────────────────────────────────
        in_reg = False
        if regression_cycle_id:
            reg_cases = self._fetch_cycle_cases(regression_cycle_id)
            in_reg = any(self._case_matches_jira_id(c, jira_id) for c in reg_cases)

        return TestExecution(
            total_cases=total,
            passed=passed,
            failed=failed,
            blocked=blocked,
            not_run=not_run,
            in_regression=in_reg,
        )

    # ------------------------------------------------------------------
    # Bulk feature enrichment
    # ------------------------------------------------------------------

    def enrich_feature(
        self,
        feature: Feature,
        cycle_id: str,
        regression_cycle_id: str = "",
    ) -> Feature:
        """Attach a :class:`TestExecution` to *feature.test_exec* in-place."""
        feature.test_exec = self.get_test_execution_summary(
            feature.jira_id, cycle_id, regression_cycle_id
        )
        return feature

    def enrich_features(
        self,
        features: List[Feature],
        cycle_id: str,
        regression_cycle_id: str = "",
    ) -> List[Feature]:
        """Bulk-enrich a list of :class:`Feature` objects."""
        return [self.enrich_feature(f, cycle_id, regression_cycle_id) for f in features]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _test_case_exists(self, jira_id: str) -> bool:
        url = f"{self.base_url}/rest/api/2/testcase/search"
        payload = {"maxResults": 1, "startAt": 0, "query": jira_id}
        try:
            resp = self._session.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            total = data.get("total", data.get("totalCount", len(data.get("data", []))))
            return int(total) > 0
        except requests.RequestException as exc:
            logger.warning("QMetry search failed for %s: %s", jira_id, exc)
            return False

    def _fetch_cycle_cases(self, cycle_id: str) -> list:
        url = f"{self.base_url}/rest/api/2/testcycle/{cycle_id}/testcase"
        try:
            resp = self._session.get(url, params={"maxResults": 500})
            resp.raise_for_status()
            return resp.json().get("data", [])
        except requests.RequestException as exc:
            logger.warning("QMetry cycle fetch failed (cycle=%s): %s", cycle_id, exc)
            return []

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
