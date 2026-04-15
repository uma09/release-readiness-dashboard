"""
GitHub Service — fetches merged PRs between two branches using PyGithub
and returns a list of Feature objects with Jira IDs extracted from PR titles.
"""

import logging
import re
from typing import List, Optional

from github import Github, GithubException

from models import Feature

logger = logging.getLogger(__name__)

# Matches Jira-style keys like PROJ-123 or ABC-4567
_JIRA_ID_RE = re.compile(r"[A-Z]+-\d+")

# Directories / path prefixes that qualify a file as part of a "core module".
# Override via Config.CORE_MODULE_PATHS at instantiation time.
_DEFAULT_CORE_PATHS = (
    "core/",
    "src/core/",
    "lib/",
    "common/",
    "shared/",
    "api/",
    "auth/",
    "payment/",
    "database/",
    "models/",
)


class GitHubService:
    def __init__(
        self,
        token: str,
        repo: str,
        platform: str = "",
        core_module_paths: tuple = _DEFAULT_CORE_PATHS,
        force_core_module: bool = False,
    ) -> None:
        """
        Args:
            token:              GitHub personal-access token or GitHub App token.
            repo:               Repository in ``owner/name`` format,
                                e.g. ``NBCUDTC/gst-apps-android``.
            platform:           Label stamped on every Feature:
                                ``"android"``, ``"ios"``, or ``"config"``.
            core_module_paths:  Path prefixes that classify a file as a core module.
            force_core_module:  When True *every* feature from this repo gets
                                ``core_module=True`` regardless of file paths.
                                Used for the config repo (all config changes
                                are implicitly core and affect both platforms).
        """
        self._gh = Github(token)
        self._repo_name = repo
        self._repo = self._gh.get_repo(repo)
        self._platform = platform
        self._core_paths = core_module_paths
        self._force_core_module = force_core_module

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_merged_features(
        self,
        base_branch: str = "main",
        head_branch: str = "develop",
    ) -> List[Feature]:
        """Return Feature objects for every merged PR from *head_branch* into *base_branch*.

        Workflow
        --------
        1. Compare ``base_branch``..``head_branch`` to find commits unique to the head.
        2. For each commit, look up its associated merged PR.
        3. Extract a Jira ticket ID from the PR title (pattern ``[A-Z]+-\\d+``).
        4. List the files changed by that PR and infer ``core_module`` status.

        Only PRs that carry a Jira ID in their title are included.
        """
        try:
            comparison = self._repo.compare(base_branch, head_branch)
        except GithubException as exc:
            logger.error("GitHub compare failed (%s..%s): %s", base_branch, head_branch, exc)
            return []

        seen_prs: set = set()
        features: List[Feature] = []

        for commit in comparison.commits:
            for pr in commit.get_pulls():
                if pr.number in seen_prs:
                    continue
                if pr.merged_at is None:
                    continue
                seen_prs.add(pr.number)

                jira_id = self._extract_jira_id(pr.title)
                if not jira_id:
                    logger.debug("PR #%d has no Jira ID in title, skipping.", pr.number)
                    continue

                files_changed = self._get_files_changed(pr.number)
                is_core = self._force_core_module or self._is_core_module(files_changed)
                features.append(
                    Feature(
                        jira_id=jira_id,
                        pr_number=pr.number,
                        platform=self._platform,
                        repo=self._repo_name,
                        affects_both_platforms=(self._platform == "config"),
                        files_changed=files_changed,
                        core_module=is_core,
                    )
                )
                logger.info(
                    "[%s] Discovered feature %s from PR #%d",
                    self._platform or self._repo_name, jira_id, pr.number,
                )

        return features

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_jira_id(title: str) -> Optional[str]:
        """Return the first Jira-style ID found in *title*, or ``None``."""
        match = _JIRA_ID_RE.search(title)
        return match.group(0) if match else None

    def _get_files_changed(self, pr_number: int) -> List[str]:
        """Return list of file paths changed by a PR."""
        try:
            pr = self._repo.get_pull(pr_number)
            return [f.filename for f in pr.get_files()]
        except GithubException as exc:
            logger.warning("Could not fetch files for PR #%d: %s", pr_number, exc)
            return []

    def _is_core_module(self, files: List[str]) -> bool:
        """Return True if any changed file lives under a core-module path prefix."""
        return any(
            f.startswith(prefix) for f in files for prefix in self._core_paths
        )
