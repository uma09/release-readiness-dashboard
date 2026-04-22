"""
GitHub Service — reads data from LOCAL git clones instead of the GitHub REST API.

Authentication is handled transparently by the SSH key (~/.ssh/id_nbcu) that
is already loaded in the macOS Keychain — the same key GitHub Desktop and the
qa-certification-skill scripts use.  No GitHub PAT is required.

Local repo layout (default):
    ~/repos/gst-apps-android
    ~/repos/gst-apps-ios
    ~/repos/peacock-mobile-config   ← optional; skipped gracefully if absent

Branch listing  : git fetch + git branch -r
Commit diff     : git log origin/base..origin/head
File stats      : git diff --name-only + git diff --shortstat
"""

import logging
import os
import re
import subprocess
from typing import List, Optional, Tuple

from models import Feature, PRDiff

logger = logging.getLogger(__name__)

# Matches Jira-style ticket keys e.g. MOB-1234, PCOCK-56
_JIRA_ID_RE = re.compile(r"[A-Z]+-\d+")

_DEFAULT_CORE_PATHS = (
    "core/", "src/core/", "lib/", "common/", "shared/",
    "api/", "auth/", "payment/", "database/", "models/",
)
_SOURCE_EXTS = frozenset({
    ".kt", ".java", ".swift", ".m", ".mm",
    ".py", ".js", ".ts", ".tsx", ".go", ".rb",
    ".cpp", ".c", ".h", ".cs",
})
_CONFIG_EXTS = frozenset({
    ".json", ".yaml", ".yml", ".xml", ".gradle",
    ".plist", ".pbxproj", ".properties", ".env",
    ".toml", ".ini", ".xcconfig",
})
_TEST_MARKERS = ("test", "spec", "Test", "Spec", "__tests__", "androidTest", "iosTest")


# ---------------------------------------------------------------------------
# Low-level git helper
# ---------------------------------------------------------------------------

def _git(args: List[str], cwd: str) -> Tuple[str, int]:
    """Run a git command in *cwd*. Returns (stdout, returncode)."""
    try:
        r = subprocess.run(
            ["git"] + args, cwd=cwd,
            capture_output=True, text=True, timeout=60,
        )
        return r.stdout.strip(), r.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("git %s failed: %s", " ".join(args), exc)
        return "", 1


def _is_git_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


# ---------------------------------------------------------------------------
# Public helper used by app.py for branch dropdowns
# ---------------------------------------------------------------------------

def get_local_branches(repo_path: str) -> List[str]:
    """Return sorted remote branch names from a local git clone.

    Runs ``git fetch --all`` first so the list reflects the latest state on
    the remote.  Returns an empty list if the path is not a git repo or if
    the fetch/branch command fails.
    """
    if not _is_git_repo(repo_path):
        logger.warning("Not a git repo (or not found): %s", repo_path)
        return []

    # Best-effort fetch — ignore failures (e.g. no network)
    _git(["fetch", "--all", "--quiet"], repo_path)

    stdout, rc = _git(["branch", "-r", "--format=%(refname:short)"], repo_path)
    if rc != 0 or not stdout:
        return []

    priority = ["main", "master", "develop", "release", "staging"]
    branches = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or "HEAD" in line:
            continue
        name = line.removeprefix("origin/")
        if name:
            branches.append(name)

    unique = sorted(set(branches))
    top  = [b for b in priority if b in unique]
    rest = [b for b in unique  if b not in priority]
    return top + rest


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------

class GitHubService:
    def __init__(
        self,
        token: str,               # kept for API compat — not used; SSH handles auth
        repo: str,                # "NBCUDTC/gst-apps-android" — used as a label
        local_path: str = "",     # absolute path to the local clone
        platform: str = "",
        core_module_paths: tuple = _DEFAULT_CORE_PATHS,
        force_core_module: bool = False,
    ) -> None:
        self._repo_name       = repo
        self._local_path      = local_path
        self._platform        = platform
        self._core_paths      = core_module_paths
        self._force_core_module = force_core_module

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_merged_features(
        self,
        base_branch: str = "develop",
        head_branch: str = "main",
    ) -> List[Feature]:
        """Return Feature objects for commits in *head_branch* not in *base_branch*.

        Uses ``git log origin/base..origin/head`` on the local clone.
        Each commit subject line is scanned for a Jira ID (e.g. MOB-1234).
        Diff stats are computed once for the full branch range.
        """
        if not self._local_path or not _is_git_repo(self._local_path):
            logger.warning("[%s] Local repo not found: %s", self._platform, self._local_path)
            return []

        # Refresh remote refs
        _git(["fetch", "--all", "--quiet"], self._local_path)

        base_ref = f"origin/{base_branch}"
        head_ref = f"origin/{head_branch}"

        log_out, rc = _git(
            ["log", f"{base_ref}..{head_ref}", "--format=%H|||%s"],
            self._local_path,
        )
        if rc != 0 or not log_out:
            logger.info(
                "[%s] No commits between %s..%s (branches may not exist yet)",
                self._platform, base_branch, head_branch,
            )
            return []

        # Build one range-level PRDiff (shared across all features in this repo)
        range_diff = self._build_range_diff(base_ref, head_ref)

        seen_jira: set = set()
        features: List[Feature] = []
        pr_counter = 0

        for line in log_out.splitlines():
            parts = line.split("|||", 1)
            if len(parts) < 2:
                continue
            commit_hash, subject = parts

            jira_id = _extract_jira_id(subject)
            if not jira_id or jira_id in seen_jira:
                continue
            seen_jira.add(jira_id)
            pr_counter += 1

            # Files touched by this specific commit
            files_out, _ = _git(
                ["diff-tree", "--no-commit-id", "-r", "--name-only", commit_hash],
                self._local_path,
            )
            files_changed = [f for f in files_out.splitlines() if f]
            is_core = self._force_core_module or self._is_core_module(files_changed)

            features.append(Feature(
                jira_id=jira_id,
                pr_number=pr_counter,        # sequential — no PR numbers in local git
                platform=self._platform,
                repo=self._repo_name,
                affects_both_platforms=(self._platform == "config"),
                files_changed=files_changed,
                core_module=is_core,
                pr_diff=range_diff,
            ))

        logger.info(
            "[%s] %d feature(s) found between %s..%s",
            self._platform, len(features), base_branch, head_branch,
        )
        return features

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_range_diff(self, base_ref: str, head_ref: str) -> PRDiff:
        """Compute aggregate diff stats for the full branch range."""
        source = test = config = 0

        files_out, _ = _git(
            ["diff", "--name-only", f"{base_ref}...{head_ref}"],
            self._local_path,
        )
        all_files = [f for f in files_out.splitlines() if f]

        for name in all_files:
            ext = os.path.splitext(name)[1].lower()
            if any(m in name for m in _TEST_MARKERS):
                test += 1
            elif ext in _SOURCE_EXTS:
                source += 1
            elif ext in _CONFIG_EXTS:
                config += 1

        stat_out, _ = _git(
            ["diff", "--shortstat", f"{base_ref}...{head_ref}"],
            self._local_path,
        )
        lines_added = lines_removed = 0
        if stat_out:
            m = re.search(r"(\d+) insertion", stat_out)
            if m:
                lines_added = int(m.group(1))
            m = re.search(r"(\d+) deletion", stat_out)
            if m:
                lines_removed = int(m.group(1))

        return PRDiff(
            lines_added=lines_added,
            lines_removed=lines_removed,
            total_files=len(all_files),
            source_files=source,
            test_files=test,
            config_files=config,
            has_test_changes=(test > 0),
            days_open=0,
            review_count=0,
        )

    def _is_core_module(self, files: List[str]) -> bool:
        return any(f.startswith(p) for f in files for p in self._core_paths)


def _extract_jira_id(text: str) -> Optional[str]:
    match = _JIRA_ID_RE.search(text)
    return match.group(0) if match else None
