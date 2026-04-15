"""
Jira Service — fetches issue/bug data for a given release version
and enriches Feature objects with per-ticket status and sprint information.
"""

import logging
from typing import Any, Dict, List, Optional

import requests
from requests.auth import HTTPBasicAuth

from models import Feature, JiraData, JiraIssue

logger = logging.getLogger(__name__)


class JiraService:
    def __init__(self, base_url: str, user: str, token: str) -> None:
        """
        Args:
            base_url: Jira instance root URL, e.g. ``https://org.atlassian.net``.
            user:     Jira account email address.
            token:    Jira API token.
        """
        self.base_url = base_url.rstrip("/")
        self._auth = HTTPBasicAuth(user, token)
        self._session = requests.Session()
        self._session.auth = self._auth
        self._session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_release_data(self) -> Dict[str, Any]:
        """Return a plain dict suitable for JSON serialisation."""
        data = self._fetch_release_data()
        return {
            "open_blockers": [vars(i) for i in data.open_blockers],
            "open_critical_bugs": [vars(i) for i in data.open_critical_bugs],
            "total_issues": data.total_issues,
            "closed_issues": data.closed_issues,
            "release_version": data.release_version,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_release_data(self, version: str = "") -> JiraData:
        blockers = self._search_issues(priority="Blocker", status_not="Done")
        criticals = self._search_issues(priority="Critical", issue_type="Bug", status_not="Done")
        all_issues = self._search_issues()
        closed = self._search_issues(status="Done")

        return JiraData(
            open_blockers=blockers,
            open_critical_bugs=criticals,
            total_issues=len(all_issues),
            closed_issues=len(closed),
            release_version=version,
        )

    def _search_issues(
        self,
        priority: str = "",
        issue_type: str = "",
        status: str = "",
        status_not: str = "",
        max_results: int = 200,
    ) -> List[JiraIssue]:
        jql_parts: List[str] = []
        if priority:
            jql_parts.append(f'priority = "{priority}"')
        if issue_type:
            jql_parts.append(f'issuetype = "{issue_type}"')
        if status:
            jql_parts.append(f'status = "{status}"')
        if status_not:
            jql_parts.append(f'status != "{status_not}"')

        jql = " AND ".join(jql_parts) if jql_parts else "order by created DESC"
        url = f"{self.base_url}/rest/api/3/search"

        try:
            response = self._session.get(
                url,
                params={"jql": jql, "maxResults": max_results, "fields": "summary,status,priority,issuetype"},
            )
            response.raise_for_status()
            return [self._parse_issue(i) for i in response.json().get("issues", [])]
        except requests.RequestException as exc:
            logger.error("Jira search failed (jql=%r): %s", jql, exc)
            return []

    def _parse_issue(self, raw: dict) -> JiraIssue:
        fields = raw.get("fields", {})
        return JiraIssue(
            key=raw["key"],
            summary=fields.get("summary", ""),
            status=fields.get("status", {}).get("name", "Unknown"),
            priority=fields.get("priority", {}).get("name", "Unknown"),
            issue_type=fields.get("issuetype", {}).get("name", "Unknown"),
            url=f"{self.base_url}/browse/{raw['key']}",
        )

    # ------------------------------------------------------------------
    # Per-ticket enrichment (Step 4)
    # ------------------------------------------------------------------

    def get_issue_details(self, jira_id: str) -> Dict[str, Any]:
        """Fetch status, sprint, and summary for a single Jira ticket.

        Returns a dict with keys: ``status``, ``sprint``, ``summary``.
        Falls back to empty strings on error so callers never have to guard.
        """
        url = f"{self.base_url}/rest/api/3/issue/{jira_id}"
        fields_param = "summary,status,sprint,customfield_10020"  # cf 10020 = Sprint (classic)
        try:
            response = self._session.get(url, params={"fields": fields_param})
            response.raise_for_status()
            raw = response.json()
            fields = raw.get("fields", {})

            # Sprint may live in customfield_10020 (list) or the "sprint" field
            sprint = self._extract_sprint_name(fields)

            return {
                "status": fields.get("status", {}).get("name", ""),
                "sprint": sprint,
                "summary": fields.get("summary", ""),
            }
        except requests.RequestException as exc:
            logger.warning("Could not fetch Jira issue %s: %s", jira_id, exc)
            return {"status": "", "sprint": "", "summary": ""}

    def enrich_feature(self, feature: Feature) -> Feature:
        """Enrich a :class:`Feature` in-place with Jira status/sprint/summary."""
        details = self.get_issue_details(feature.jira_id)
        feature.jira_status = details["status"]
        feature.jira_sprint = details["sprint"]
        feature.jira_summary = details["summary"]
        return feature

    def enrich_features(self, features: List[Feature]) -> List[Feature]:
        """Bulk-enrich a list of :class:`Feature` objects from Jira."""
        return [self.enrich_feature(f) for f in features]

    # ------------------------------------------------------------------
    # Sprint extraction helper
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sprint_name(fields: dict) -> str:
        """Pull the active sprint name out of the Jira field payload."""
        # Jira Cloud: customfield_10020 is a list of sprint objects
        sprints = fields.get("customfield_10020") or []
        if isinstance(sprints, list) and sprints:
            # Last entry is the most recent sprint
            last = sprints[-1]
            if isinstance(last, dict):
                return last.get("name", "")
            # Some configurations return a raw string like "com.atlassian.greenhopper…name=Sprint 3,…"
            if isinstance(last, str):
                match = __import__("re").search(r"name=([^,\]]+)", last)
                return match.group(1) if match else last
        return ""
