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

# ---------------------------------------------------------------------------
# Risk scoring weights
# ---------------------------------------------------------------------------
#
# Each "bad" signal adds points → higher score = higher risk.
#
#   No test case exists  : +4   (nothing to run even if we wanted to)
#   Not executed         : +3   (test exists but was never run)
#   Not in regression    : +2   (not part of the safety net)
#   Core module touched  : +2   (blast radius multiplier)
#
# Maximum possible score = 11
#
_W_NO_TEST_CASE   = 4
_W_NOT_EXECUTED   = 3
_W_NOT_REGRESSION = 2
_W_CORE_MODULE    = 2


def calculate_risk(feature: Feature) -> Feature:
    """Compute ``risk_score`` and ``risk_level`` for *feature* and return it.

    Scoring table (higher = riskier):
    ┌─────────────────────────────┬────────┐
    │ Signal                      │ Points │
    ├─────────────────────────────┼────────┤
    │ test_case_exists = False    │   +4   │
    │ executed         = False    │   +3   │
    │ in_regression    = False    │   +2   │
    │ core_module      = True     │   +2   │
    └─────────────────────────────┴────────┘

    Risk levels:
      0 – 2  → LOW
      3 – 5  → MEDIUM
      6 – 8  → HIGH
      9+     → CRITICAL
    """
    score = 0

    if not feature.test_case_exists:
        score += _W_NO_TEST_CASE
    if not feature.executed:
        score += _W_NOT_EXECUTED
    if not feature.in_regression:
        score += _W_NOT_REGRESSION
    if feature.core_module:
        score += _W_CORE_MODULE

    if score <= 2:
        level = RiskLevel.LOW
    elif score <= 5:
        level = RiskLevel.MEDIUM
    elif score <= 8:
        level = RiskLevel.HIGH
    else:
        level = RiskLevel.CRITICAL

    feature.risk_score = score
    feature.risk_level = level.value
    return feature


def calculate_risk_for_all(features: List[Feature]) -> List[Feature]:
    """Apply :func:`calculate_risk` to every feature in *features* in-place."""
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
