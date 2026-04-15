"""
Data models for the Release Readiness Dashboard.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional
from enum import Enum


class ReadinessLevel(str, Enum):
    GO = "GO"
    CONDITIONAL = "CONDITIONAL"
    NO_GO = "NO_GO"
    UNKNOWN = "UNKNOWN"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Core feature model — single source of truth shared by all layers
# ---------------------------------------------------------------------------

@dataclass
class Feature:
    """Represents one development feature tied to a Jira ticket and a PR.

    Fields populated progressively as each service layer enriches the object:
      - GitHub layer fills : jira_id, pr_number, files_changed, core_module,
                             platform, repo, affects_both_platforms
      - Jira layer fills   : jira_status, jira_sprint, jira_summary
      - QMetry layer fills : test_case_exists, executed, in_regression
      - Governance layer   : risk_score, risk_level

    Platform values
    ---------------
      "android"  — from NBCUDTC/gst-apps-android
      "ios"      — from NBCUDTC/gst-apps-ios
      "config"   — from NBCUDTC/peacock-mobile-config (affects BOTH platforms)
    """

    # ── Source / identity ────────────────────────────────────────────────────
    jira_id: str = ""
    pr_number: int = 0
    platform: str = ""          # "android" | "ios" | "config"
    repo: str = ""              # full slug, e.g. "NBCUDTC/gst-apps-android"
    affects_both_platforms: bool = False   # True for every config-repo feature

    # ── GitHub-sourced ───────────────────────────────────────────────────────
    files_changed: List[str] = field(default_factory=list)
    core_module: bool = False

    # ── QMetry-sourced (boolean flags only) ──────────────────────────────────
    test_case_exists: bool = False
    executed: bool = False
    in_regression: bool = False

    # ── Governance-computed ──────────────────────────────────────────────────
    risk_score: int = 0
    risk_level: str = RiskLevel.UNKNOWN.value

    # ── Jira enrichment ──────────────────────────────────────────────────────
    jira_status: str = ""
    jira_sprint: str = ""
    jira_summary: str = ""


# ---------------------------------------------------------------------------
# GitHub models
# ---------------------------------------------------------------------------

@dataclass
class PRData:
    pr_number: int
    title: str
    state: str                   # "open" | "closed" | "merged"
    approvals: int
    has_failing_checks: bool
    url: str


@dataclass
class GitHubData:
    open_prs: List[PRData] = field(default_factory=list)
    failing_checks: int = 0
    total_prs: int = 0
    branch: str = ""


# ---------------------------------------------------------------------------
# Jira models
# ---------------------------------------------------------------------------

@dataclass
class JiraIssue:
    key: str
    summary: str
    status: str
    priority: str
    issue_type: str
    url: str


@dataclass
class JiraData:
    open_blockers: List[JiraIssue] = field(default_factory=list)
    open_critical_bugs: List[JiraIssue] = field(default_factory=list)
    total_issues: int = 0
    closed_issues: int = 0
    release_version: str = ""


# ---------------------------------------------------------------------------
# QMetry models
# ---------------------------------------------------------------------------

@dataclass
class TestCycleData:
    cycle_id: str
    cycle_name: str
    total_tests: int
    passed: int
    failed: int
    blocked: int
    not_run: int

    @property
    def pass_rate(self) -> float:
        if self.total_tests == 0:
            return 0.0
        return round((self.passed / self.total_tests) * 100, 2)


@dataclass
class QMetryData:
    cycles: List[TestCycleData] = field(default_factory=list)
    overall_pass_rate: float = 0.0
    total_tests: int = 0
    total_passed: int = 0
    total_failed: int = 0


# ---------------------------------------------------------------------------
# Governance / aggregated status
# ---------------------------------------------------------------------------

@dataclass
class GovernanceCheck:
    name: str
    passed: bool
    message: str
    details: Optional[str] = None


@dataclass
class ReleaseStatus:
    readiness: ReadinessLevel = ReadinessLevel.UNKNOWN
    checks: List[GovernanceCheck] = field(default_factory=list)
    github: Optional[GitHubData] = None
    jira: Optional[JiraData] = None
    qmetry: Optional[QMetryData] = None

    def to_dict(self) -> dict:
        return {
            "readiness": self.readiness.value,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "details": c.details,
                }
                for c in self.checks
            ],
            "github": asdict(self.github) if self.github else None,
            "jira": asdict(self.jira) if self.jira else None,
            "qmetry": asdict(self.qmetry) if self.qmetry else None,
        }
