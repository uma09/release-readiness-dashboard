"""
GitHub Service — fetches merged PRs between two branches using PyGithub,
extracts Jira IDs from PR titles, and attaches rich diff statistics (PRDiff)
to each Feature object.
"""

import logging
import os
import re
from typing import List, Optional

from github import Github, GithubException

from models import Feature, PRDiff

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

        Each Feature carries a :class:`PRDiff` with full diff statistics
        (lines added/removed, file categories, test-coverage signal, PR velocity).
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

                pr_diff = self._build_pr_diff(pr)
                files_changed = [f for f in _filenames_from_diff(pr_diff, pr)]
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
                        pr_diff=pr_diff,
                    )
                )
                logger.info(
                    "[%s] feature=%s PR=#%d churn=%d test_changes=%s",
                    self._platform, jira_id, pr.number,
                    pr_diff.churn, pr_diff.has_test_changes,
                )

        return features

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    _SOURCE_EXTS = frozenset({
        ".kt", ".java", ".swift", ".m", ".mm",           # mobile
        ".py", ".js", ".ts", ".tsx", ".go", ".rb",       # backend / scripts
        ".cpp", ".c", ".h", ".cs",                       # native
    })
    _CONFIG_EXTS = frozenset({
        ".json", ".yaml", ".yml", ".xml", ".gradle",
        ".plist", ".pbxproj", ".properties", ".env",
        ".toml", ".ini", ".xcconfig",
    })
    _TEST_MARKERS = ("test", "spec", "Test", "Spec", "__tests__", "androidTest", "iosTest")

    def _build_pr_diff(self, pr) -> PRDiff:
        """Build a :class:`PRDiff` from a PyGithub PullRequest object."""
        source = test = config = 0
        try:
            gh_files = list(pr.get_files())
        except GithubException as exc:
            logger.warning("Could not fetch files for PR #%d: %s", pr.number, exc)
            gh_files = []

        for f in gh_files:
            name = f.filename
            ext  = os.path.splitext(name)[1].lower()
            if any(m in name for m in self._TEST_MARKERS):
                test += 1
            elif ext in self._SOURCE_EXTS:
                source += 1
            elif ext in self._CONFIG_EXTS:
                config += 1

        days_open = 0
        if pr.created_at and pr.merged_at:
            days_open = max(0, (pr.merged_at - pr.created_at).days)

        try:
            review_count = sum(1 for _ in pr.get_reviews())
        except GithubException:
            review_count = 0

        return PRDiff(
            lines_added=pr.additions,
            lines_removed=pr.deletions,
            total_files=pr.changed_files,
            source_files=source,
            test_files=test,
            config_files=config,
            has_test_changes=(test > 0),
            days_open=days_open,
            review_count=review_count,
        )

    @staticmethod
    def _extract_jira_id(title: str) -> Optional[str]:
        match = _JIRA_ID_RE.search(title)
        return match.group(0) if match else None

    def _is_core_module(self, files: List[str]) -> bool:
        return any(f.startswith(p) for f in files for p in self._core_paths)


def _filenames_from_diff(pr_diff: PRDiff, pr) -> List[str]:
    """Re-use already-fetched file list; fall back to an empty list on error."""
    try:
        return [f.filename for f in pr.get_files()]
    except GithubException:
        return []
