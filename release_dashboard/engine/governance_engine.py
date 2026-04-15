"""
Governance Engine — two responsibilities:

1. calculate_risk(feature)  — per-feature risk scoring (LOW / MEDIUM / HIGH / CRITICAL)
2. GovernanceEngine.evaluate(…) — aggregate release readiness verdict (GO / CONDITIONAL / NO-GO)
"""

import logging
from typing import Any, Dict, List

from config import Config
from models import (
    Feature,
    GovernanceCheck,
    ReadinessLevel,
    ReleaseStatus,
    RiskLevel,
)

logger = logging.getLogger(__name__)


def _score_code_risk(feature: Feature) -> int:
    """Score 0-10 based on PR diff signals.

    Signal                                Points
    ─────────────────────────────────────────────
    core_module touched                    +2
    affects_both_platforms (config repo)   +1
    Churn > 500 lines                      +2
    Churn 200-500 lines                    +1
    Source changes with NO test changes    +2
    Config files changed                   +1
    PR open > 14 days (stale/risky)        +2
    PR open 7-14 days                      +1
    """
    score = 0
    diff = feature.pr_diff

    if feature.core_module:
        score += 2
    if feature.affects_both_platforms:
        score += 1

    if diff:
        if diff.churn > 500:
            score += 2
        elif diff.churn > 200:
            score += 1
        if diff.source_files > 0 and not diff.has_test_changes:
            score += 2
        if diff.config_files > 0:
            score += 1
        if diff.days_open > 14:
            score += 2
        elif diff.days_open > 7:
            score += 1

    return min(score, 10)


def _score_test_risk(feature: Feature) -> int:
    """Score 0-10 based on QMetry test execution data.

    Signal                                Points
    ─────────────────────────────────────────────
    No test case exists at all             +5
    Test exists but never executed         +3
    Not in regression suite                +2
    Has failed test cases                  +2
    Has blocked test cases                 +1
    Pass rate < 100% (but > 0%)           +1
    """
    score = 0
    te = feature.test_exec

    if te is None or not te.test_case_exists:
        score += 5
    else:
        if not te.executed:
            score += 3
        if not te.in_regression:
            score += 2
        if te.failed > 0:
            score += 2
        if te.blocked > 0:
            score += 1
        if 0 < te.pass_rate < 100:
            score += 1

    return min(score, 10)


def _score_jira_risk(feature: Feature) -> int:
    """Score 0-10 based on Jira metadata.

    Signal                                Points
    ─────────────────────────────────────────────
    Priority Blocker                       +4
    Priority Critical                      +3
    Priority High                          +2
    Priority Medium                        +1
    Issue type is Bug / Defect             +2
    Status not Done / Closed / Resolved    +2
    No fix version set                     +1
    No story points                        +1
    """
    score = 0
    jm = feature.jira_meta

    if jm is None:
        return 4   # unknown metadata → conservative medium-high score

    _priority_pts = {"Blocker": 4, "Critical": 3, "High": 2, "Medium": 1, "Low": 0}
    score += _priority_pts.get(jm.priority, 1)

    if jm.issue_type in ("Bug", "Defect"):
        score += 2

    _done_statuses = {"Done", "Closed", "Resolved", "Released", "Won't Fix"}
    if jm.status not in _done_statuses:
        score += 2

    if not jm.fix_version:
        score += 1
    if jm.story_points == 0:
        score += 1

    return min(score, 10)


def calculate_risk(feature: Feature) -> Feature:
    """Compute multi-dimensional risk scores and set risk_level on *feature*.

    Weighted composite
    ──────────────────
      test_risk  ×  0.50   (biggest driver — no tests = ship blind)
      code_risk  ×  0.30   (churn, core modules, PR staleness)
      jira_risk  ×  0.20   (priority, bug type, no fix version)

    Risk levels  (0-10 scale)
    ──────────────────────────
      0 – 2  →  LOW
      3 – 4  →  MEDIUM
      5 – 7  →  HIGH
      8 – 10 →  CRITICAL
    """
    code  = _score_code_risk(feature)
    test  = _score_test_risk(feature)
    jira  = _score_jira_risk(feature)
    composite = round(code * 0.30 + test * 0.50 + jira * 0.20)

    if composite <= 2:
        level = RiskLevel.LOW
    elif composite <= 4:
        level = RiskLevel.MEDIUM
    elif composite <= 7:
        level = RiskLevel.HIGH
    else:
        level = RiskLevel.CRITICAL

    feature.code_risk_score = code
    feature.test_risk_score = test
    feature.jira_risk_score = jira
    feature.risk_score      = composite
    feature.risk_level      = level.value
    return feature


def calculate_risk_for_all(features: List[Feature]) -> List[Feature]:
    """Apply :func:`calculate_risk` to every feature in *features*."""
    return [calculate_risk(f) for f in features]


class GovernanceEngine:
    """Applies configurable governance rules across GitHub, Jira and QMetry data.

    Use :func:`calculate_risk` (module-level) for per-feature risk scoring.
    Use :meth:`evaluate` for the overall release readiness verdict.
    """

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def evaluate(
        self,
        github_data: Dict[str, Any],
        jira_data: Dict[str, Any],
        qmetry_data: Dict[str, Any],
    ) -> ReleaseStatus:
        """
        Run all governance checks and return a :class:`ReleaseStatus`.

        Args:
            github_data:  Raw dict returned by :meth:`GitHubService.get_release_data`.
            jira_data:    Raw dict returned by :meth:`JiraService.get_release_data`.
            qmetry_data:  Raw dict returned by :meth:`QMetryService.get_release_data`.

        Returns:
            A :class:`ReleaseStatus` with a readiness verdict and individual check results.
        """
        checks = []
        checks += self._check_github(github_data)
        checks += self._check_jira(jira_data)
        checks += self._check_qmetry(qmetry_data)

        readiness = self._derive_readiness(checks)

        return ReleaseStatus(
            readiness=readiness,
            checks=checks,
        )

    # ------------------------------------------------------------------
    # GitHub checks
    # ------------------------------------------------------------------

    def _check_github(self, data: Dict[str, Any]) -> list:
        checks = []

        # 1. No failing CI checks
        failing = data.get("failing_checks", 0)
        checks.append(
            GovernanceCheck(
                name="CI Checks Passing",
                passed=failing == 0,
                message=f"{failing} PR(s) have failing CI checks." if failing else "All CI checks are passing.",
            )
        )

        # 2. Minimum PR approvals
        open_prs = data.get("open_prs", [])
        under_approved = [
            pr for pr in open_prs if pr.get("approvals", 0) < Config.REQUIRED_PR_APPROVALS
        ]
        checks.append(
            GovernanceCheck(
                name="PR Approvals",
                passed=len(under_approved) == 0,
                message=(
                    f"{len(under_approved)} open PR(s) below the required {Config.REQUIRED_PR_APPROVALS} approval(s)."
                    if under_approved
                    else f"All open PRs meet the minimum {Config.REQUIRED_PR_APPROVALS} approval(s)."
                ),
                details=str([pr.get("pr_number") for pr in under_approved]) if under_approved else None,
            )
        )

        return checks

    # ------------------------------------------------------------------
    # Jira checks
    # ------------------------------------------------------------------

    def _check_jira(self, data: Dict[str, Any]) -> list:
        checks = []

        blockers = data.get("open_blockers", [])
        checks.append(
            GovernanceCheck(
                name="No Open Blockers",
                passed=len(blockers) <= Config.MAX_OPEN_BLOCKERS,
                message=(
                    f"{len(blockers)} open blocker(s) found." if blockers else "No open blockers."
                ),
                details=str([i.get("key") for i in blockers]) if blockers else None,
            )
        )

        criticals = data.get("open_critical_bugs", [])
        checks.append(
            GovernanceCheck(
                name="No Open Critical Bugs",
                passed=len(criticals) <= Config.MAX_OPEN_CRITICAL_BUGS,
                message=(
                    f"{len(criticals)} open critical bug(s) found." if criticals else "No open critical bugs."
                ),
                details=str([i.get("key") for i in criticals]) if criticals else None,
            )
        )

        return checks

    # ------------------------------------------------------------------
    # QMetry checks
    # ------------------------------------------------------------------

    def _check_qmetry(self, data: Dict[str, Any]) -> list:
        checks = []

        pass_rate = data.get("overall_pass_rate", 0.0)
        checks.append(
            GovernanceCheck(
                name="Test Pass Rate",
                passed=pass_rate >= Config.MIN_TEST_PASS_RATE,
                message=(
                    f"Pass rate is {pass_rate}%, below the required {Config.MIN_TEST_PASS_RATE}%."
                    if pass_rate < Config.MIN_TEST_PASS_RATE
                    else f"Pass rate is {pass_rate}% — meets the {Config.MIN_TEST_PASS_RATE}% threshold."
                ),
            )
        )

        return checks

    # ------------------------------------------------------------------
    # Verdict derivation
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_readiness(checks: list) -> ReadinessLevel:
        if not checks:
            return ReadinessLevel.UNKNOWN
        failed = [c for c in checks if not c.passed]
        if not failed:
            return ReadinessLevel.GO
        # Critical checks that immediately block a release
        hard_stops = {"No Open Blockers", "CI Checks Passing", "No Open Critical Bugs"}
        if any(c.name in hard_stops for c in failed):
            return ReadinessLevel.NO_GO
        return ReadinessLevel.CONDITIONAL
