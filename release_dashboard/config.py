"""
Configuration for the Peacock Mobile Release Readiness Dashboard.

Three source repositories
─────────────────────────
  Android : NBCUDTC/gst-apps-android
  iOS     : NBCUDTC/gst-apps-ios
  Config  : NBCUDTC/peacock-mobile-config   ← affects BOTH platforms

All sensitive values are read from environment variables.
"""

import os


def _split(env_key: str, default: str) -> tuple:
    """Read a comma-separated env var and return a tuple of stripped strings."""
    return tuple(p.strip() for p in os.getenv(env_key, default).split(",") if p.strip())


class Config:
    # ── Application ──────────────────────────────────────────────────────────
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    PORT: int = int(os.getenv("PORT", "5000"))
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")

    # ── GitHub ───────────────────────────────────────────────────────────────
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

    # Repository slugs (org/repo) — used as labels only; auth is via SSH key
    GITHUB_REPO_ANDROID: str = os.getenv("GITHUB_REPO_ANDROID", "NBCUDTC/gst-apps-android")
    GITHUB_REPO_IOS: str     = os.getenv("GITHUB_REPO_IOS",     "NBCUDTC/gst-apps-ios")
    GITHUB_REPO_CONFIG: str  = os.getenv("GITHUB_REPO_CONFIG",  "NBCUDTC/peacock-mobile-config")

    # Local clone paths — same repos already on disk (pulled by GitHub Desktop / qa-certification-skill)
    # SSH key ~/.ssh/id_nbcu handles authentication transparently via git pull.
    _home = os.path.expanduser("~")
    GITHUB_LOCAL_REPO_ANDROID: str = os.getenv("GITHUB_LOCAL_REPO_ANDROID", os.path.join(_home, "repos", "gst-apps-android"))
    GITHUB_LOCAL_REPO_IOS: str     = os.getenv("GITHUB_LOCAL_REPO_IOS",     os.path.join(_home, "repos", "gst-apps-ios"))
    GITHUB_LOCAL_REPO_CONFIG: str  = os.getenv("GITHUB_LOCAL_REPO_CONFIG",  os.path.join(_home, "repos", "peacock-mobile-config"))

    # Shared base branch (the stable / production branch in every repo)
    GITHUB_BASE_BRANCH: str = os.getenv("GITHUB_BASE_BRANCH", "main")

    # Per-repo head branches (the release-candidate / integration branch)
    GITHUB_HEAD_BRANCH_ANDROID: str = os.getenv("GITHUB_HEAD_BRANCH_ANDROID", "develop")
    GITHUB_HEAD_BRANCH_IOS: str     = os.getenv("GITHUB_HEAD_BRANCH_IOS",     "develop")
    GITHUB_HEAD_BRANCH_CONFIG: str  = os.getenv("GITHUB_HEAD_BRANCH_CONFIG",  "develop")

    # ── Core-module path prefixes per platform ───────────────────────────────
    # Files matching these prefixes are flagged core_module=True (higher risk weight).
    # Config repo always forces core_module=True (handled in GitHubService).
    CORE_MODULE_PATHS_ANDROID: tuple = _split(
        "CORE_MODULE_PATHS_ANDROID",
        "core/,common/,network/,data/,domain/,auth/,payment/,"
        "app/src/main/java/,library/,shared/",
    )
    CORE_MODULE_PATHS_IOS: tuple = _split(
        "CORE_MODULE_PATHS_IOS",
        "Core/,Sources/Core/,Common/,Shared/,Network/,Domain/,"
        "Auth/,Payment/,Sources/Common/,Sources/Shared/",
    )

    # ── Jira ─────────────────────────────────────────────────────────────────
    JIRA_URL: str = os.getenv("JIRA_URL", "https://nbcudtc.atlassian.net")
    JIRA_USER: str = os.getenv("JIRA_USER", "")
    JIRA_TOKEN: str = os.getenv("JIRA_TOKEN", "")
    JIRA_PROJECT_KEY: str = os.getenv("JIRA_PROJECT_KEY", "PCOCK")

    # ── QMetry ───────────────────────────────────────────────────────────────
    QMETRY_URL: str = os.getenv("QMETRY_URL", "https://qtm4j.qmetry.com")
    QMETRY_TOKEN: str = os.getenv("QMETRY_TOKEN", "")

    # Release / sprint test cycle IDs (separate per platform)
    QMETRY_CYCLE_ID_ANDROID: str = os.getenv("QMETRY_CYCLE_ID_ANDROID", "")
    QMETRY_CYCLE_ID_IOS: str     = os.getenv("QMETRY_CYCLE_ID_IOS",     "")

    # Regression cycle IDs (optional)
    QMETRY_REGRESSION_CYCLE_ID_ANDROID: str = os.getenv("QMETRY_REGRESSION_CYCLE_ID_ANDROID", "")
    QMETRY_REGRESSION_CYCLE_ID_IOS: str     = os.getenv("QMETRY_REGRESSION_CYCLE_ID_IOS",     "")

    # ── Governance thresholds ────────────────────────────────────────────────
    MIN_TEST_PASS_RATE: float    = float(os.getenv("MIN_TEST_PASS_RATE",    "95.0"))
    MAX_OPEN_CRITICAL_BUGS: int  = int(os.getenv("MAX_OPEN_CRITICAL_BUGS",  "0"))
    MAX_OPEN_BLOCKERS: int       = int(os.getenv("MAX_OPEN_BLOCKERS",       "0"))
    REQUIRED_PR_APPROVALS: int   = int(os.getenv("REQUIRED_PR_APPROVALS",   "2"))
