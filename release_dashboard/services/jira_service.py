"""
Jira Service — fetches full issue metadata per Jira ticket and enriches
Feature objects with a JiraMetadata payload (priority, type, story points,
fix version, labels, sprint, status).
"""

import logging
import re
from typing import Any, Dict, List, Optional

import requests
from requests.auth import HTTPBasicAuth

from models import Feature, JiraData, JiraIssue, JiraMetadata

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
    # Per-ticket enrichment — full metadata
    # ------------------------------------------------------------------

    # Jira Cloud field IDs
    _FIELDS = ",".join([
        "summary", "status", "priority", "issuetype",
        "fixVersions", "labels",
        "customfield_10016",   # Story Points (Jira Cloud)
        "customfield_10020",   # Sprint (Jira Cloud)
    ])

    def get_full_metadata(self, jira_id: str) -> Optional[JiraMetadata]:
        """Fetch complete metadata for a single Jira ticket.

        Returns a :class:`JiraMetadata` or ``None`` on error so the
        caller can decide how to handle missing data.
        """
        url = f"{self.base_url}/rest/api/3/issue/{jira_id}"
        try:
            resp = self._session.get(url, params={"fields": self._FIELDS})
            resp.raise_for_status()
            f = resp.json().get("fields", {})

            # Story points — cloud field or fallback
            sp_raw = f.get("customfield_10016") or f.get("story_points") or 0
            try:
                story_points = float(sp_raw)
            except (TypeError, ValueError):
                story_points = 0.0

            # Fix version — first entry
            fix_versions = f.get("fixVersions") or []
            fix_version = fix_versions[0].get("name", "") if fix_versions else ""

            return JiraMetadata(
                summary=f.get("summary", ""),
                status=f.get("status", {}).get("name", ""),
                priority=f.get("priority", {}).get("name", ""),
                issue_type=f.get("issuetype", {}).get("name", ""),
                story_points=story_points,
                sprint=self._extract_sprint_name(f),
                fix_version=fix_version,
                labels=f.get("labels") or [],
            )
        except requests.RequestException as exc:
            logger.warning("Could not fetch Jira metadata for %s: %s", jira_id, exc)
            return None

    def enrich_feature(self, feature: Feature) -> Feature:
        """Attach a :class:`JiraMetadata` to *feature* in-place."""
        feature.jira_meta = self.get_full_metadata(feature.jira_id)
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
                match = re.search(r"name=([^,\]]+)", last)
                return match.group(1) if match else last
        return ""
