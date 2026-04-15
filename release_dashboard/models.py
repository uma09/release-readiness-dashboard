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
# Rich payload dataclasses — one per data source
# ---------------------------------------------------------------------------

@dataclass
class PRDiff:
    """Diff statistics extracted from a single GitHub Pull Request."""
    lines_added: int = 0
    lines_removed: int = 0
    total_files: int = 0
    source_files: int = 0       # .kt / .swift / .java / .py / .js / .ts …
    test_files: int = 0         # paths containing test / spec / Test / Spec
    config_files: int = 0       # .json / .yaml / .yml / .xml / .gradle / .plist …
    has_test_changes: bool = False
    days_open: int = 0          # PR created_at → merged_at
    review_count: int = 0

    @property
    def churn(self) -> int:
        """Total lines touched (additions + deletions)."""
        return self.lines_added + self.lines_removed

    @property
    def is_large_change(self) -> bool:
        """True when churn exceeds 500 lines."""
        return self.churn > 500


@dataclass
class JiraMetadata:
    """Full Jira Cloud issue metadata for a single ticket."""
    summary: str = ""
    status: str = ""            # "In Progress" | "Done" | "To Do" …
    priority: str = ""          # "Blocker" | "Critical" | "High" | "Medium" | "Low"
    issue_type: str = ""        # "Story" | "Bug" | "Task" | "Epic"
    story_points: float = 0.0
    sprint: str = ""
    fix_version: str = ""
    labels: List[str] = field(default_factory=list)


@dataclass
class TestExecution:
    """QMetry test execution summary for one Jira ticket in a given cycle."""
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    not_run: int = 0
    in_regression: bool = False

    @property
    def test_case_exists(self) -> bool:
        return self.total_cases > 0

    @property
    def executed(self) -> bool:
        return (self.passed + self.failed + self.blocked) > 0

    @property
    def pass_rate(self) -> float:
        ran = self.passed + self.failed + self.blocked
        return round(self.passed / ran * 100, 1) if ran else 0.0


# ---------------------------------------------------------------------------
# Core feature model — single source of truth shared by all layers
# ---------------------------------------------------------------------------

@dataclass
class Feature:
    """One development feature, enriched progressively by each service layer.

    Enrichment pipeline
    ───────────────────
      GitHub layer   → jira_id, pr_number, files_changed, core_module, pr_diff
      Jira layer     → jira_meta  (JiraMetadata)
      QMetry layer   → test_exec  (TestExecution)
      Governance     → code_risk_score, test_risk_score, jira_risk_score,
                       risk_score, risk_level

    Platform values
    ───────────────
      "android"  — NBCUDTC/gst-apps-android
      "ios"      — NBCUDTC/gst-apps-ios
      "config"   — NBCUDTC/peacock-mobile-config  (affects BOTH platforms)
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    jira_id: str = ""
    pr_number: int = 0
    platform: str = ""
    repo: str = ""
    affects_both_platforms: bool = False

    # ── GitHub-sourced ────────────────────────────────────────────────────────
    files_changed: List[str] = field(default_factory=list)
    core_module: bool = False
    pr_diff: Optional[PRDiff] = None

    # ── Jira-sourced ──────────────────────────────────────────────────────────
    jira_meta: Optional[JiraMetadata] = None

    # ── QMetry-sourced ────────────────────────────────────────────────────────
    test_exec: Optional[TestExecution] = None

    # ── Governance-computed ───────────────────────────────────────────────────
    code_risk_score: int = 0    # 0-10  (PR diff signals)
    test_risk_score: int = 0    # 0-10  (test coverage & execution)
    jira_risk_score: int = 0    # 0-10  (priority / type / status)
    risk_score: int = 0         # 0-10  weighted composite
    risk_level: str = RiskLevel.UNKNOWN.value

    # ── Convenience properties (delegate to rich objects) ─────────────────────
    @property
    def test_case_exists(self) -> bool:
        return self.test_exec.test_case_exists if self.test_exec else False

    @property
    def executed(self) -> bool:
        return self.test_exec.executed if self.test_exec else False

    @property
    def in_regression(self) -> bool:
        return self.test_exec.in_regression if self.test_exec else False

    @property
    def jira_status(self) -> str:
        return self.jira_meta.status if self.jira_meta else ""

    @property
    def jira_sprint(self) -> str:
        return self.jira_meta.sprint if self.jira_meta else ""

    @property
    def jira_summary(self) -> str:
        return self.jira_meta.summary if self.jira_meta else ""


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
