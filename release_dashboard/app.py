"""
Peacock Mobile Release Readiness Dashboard — Streamlit UI

Sources
───────
  🤖 Android : NBCUDTC/gst-apps-android
  🍎 iOS     : NBCUDTC/gst-apps-ios
  ⚙️ Config  : NBCUDTC/peacock-mobile-config  (affects BOTH platforms)

Run with:
  streamlit run release_dashboard/app.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import streamlit as st

from config import Config
from engine.governance_engine import calculate_risk_for_all
from models import Feature
from services.github_service import GitHubService
from services.jira_service import JiraService
from services.qmetry_service import QMetryService

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Peacock Mobile — Release Readiness",
    page_icon="🦚",
    layout="wide",
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/9/9f/Peacock_tv_logo.svg/320px-Peacock_tv_logo.svg.png", width=140)
    st.title("⚙️ Configuration")

    st.subheader("🌿 Branches")
    base_branch         = st.text_input("Base branch (all repos)",     value=Config.GITHUB_BASE_BRANCH)
    android_head        = st.text_input("🤖 Android head branch",      value=Config.GITHUB_HEAD_BRANCH_ANDROID)
    ios_head            = st.text_input("🍎 iOS head branch",          value=Config.GITHUB_HEAD_BRANCH_IOS)
    config_head         = st.text_input("⚙️ Config head branch",      value=Config.GITHUB_HEAD_BRANCH_CONFIG)

    st.subheader("🧪 QMetry Test Cycles")
    android_cycle       = st.text_input("Android cycle ID",            value=Config.QMETRY_CYCLE_ID_ANDROID)
    ios_cycle           = st.text_input("iOS cycle ID",                value=Config.QMETRY_CYCLE_ID_IOS)
    android_reg_cycle   = st.text_input("Android regression cycle",    value=Config.QMETRY_REGRESSION_CYCLE_ID_ANDROID)
    ios_reg_cycle       = st.text_input("iOS regression cycle",        value=Config.QMETRY_REGRESSION_CYCLE_ID_IOS)

    st.markdown("---")
    st.caption("🔑 Credentials are loaded from environment variables.")

# ── Title ─────────────────────────────────────────────────────────────────────
st.title("🦚 Peacock Mobile — Release Readiness Dashboard")
st.caption(
    f"Base: **{base_branch}** · "
    f"🤖 `{android_head}` · 🍎 `{ios_head}` · ⚙️ `{config_head}`"
)

# ── Helpers ───────────────────────────────────────────────────────────────────
_RISK_COLOURS = {
    "LOW":      "background-color: #d4edda; color: #155724",
    "MEDIUM":   "background-color: #fff3cd; color: #856404",
    "HIGH":     "background-color: #f8d7da; color: #721c24",
    "CRITICAL": "background-color: #6f42c1; color: #ffffff",
    "UNKNOWN":  "background-color: #e2e3e5; color: #383d41",
}

_DISPLAY_COLS = [
    "platform", "jira_id", "pr_number",
    "core_module", "affects_both_platforms",
    "test_case_exists", "executed", "in_regression",
    "risk_score", "risk_level",
    "jira_status", "jira_sprint", "jira_summary",
]

_PLATFORM_ICON = {"android": "🤖", "ios": "🍎", "config": "⚙️"}


def _style_row(row: pd.Series):
    colour = _RISK_COLOURS.get(str(row.get("risk_level", "UNKNOWN")).upper(), "")
    return [colour] * len(row)


def features_to_df(features: list) -> pd.DataFrame:
    rows = [{col: getattr(f, col, "") for col in _DISPLAY_COLS} for f in features]
    df = pd.DataFrame(rows, columns=_DISPLAY_COLS)
    # Prefix platform with icon for readability
    df["platform"] = df["platform"].map(lambda p: f"{_PLATFORM_ICON.get(p, '')} {p}")
    return df


def _metric_row(features: list, label: str = ""):
    """Render a single row of 6 metric cards for a feature slice."""
    total      = len(features)
    critical   = sum(1 for f in features if f.risk_level == "CRITICAL")
    high       = sum(1 for f in features if f.risk_level == "HIGH")
    no_test    = sum(1 for f in features if not f.test_case_exists)
    not_run    = sum(1 for f in features if not f.executed)
    in_reg     = sum(1 for f in features if f.in_regression)

    if label:
        st.markdown(f"**{label}**")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Features",          total)
    c2.metric("🟣 CRITICAL",       critical)
    c3.metric("🔴 HIGH",           high)
    c4.metric("⚠️ No Test Case",   no_test)
    c5.metric("⏸️ Not Executed",   not_run)
    c6.metric("✅ In Regression",  in_reg)


def _verdict_banner(features: list):
    """Render a GO / CONDITIONAL / NO-GO verdict banner."""
    high_risk = sum(1 for f in features if f.risk_level in ("HIGH", "CRITICAL"))
    if high_risk == 0:
        st.success("✅ **GO** — No HIGH or CRITICAL risk features detected.")
    elif high_risk <= 2:
        st.warning(f"⚠️ **CONDITIONAL** — {high_risk} feature(s) are HIGH/CRITICAL. Review before shipping.")
    else:
        st.error(f"🚫 **NO-GO** — {high_risk} features are HIGH or CRITICAL risk. Address test gaps first.")


def _feature_table(features: list):
    """Render the colour-coded risk dataframe + breakdown chart."""
    if not features:
        st.info("No features found for this platform in the selected branches.")
        return
    df = features_to_df(features)
    styled = df.style.apply(_style_row, axis=1)
    st.dataframe(styled, use_container_width=True, height=420)
    with st.expander("📈 Risk Level Breakdown"):
        breakdown = (
            pd.Series([f.risk_level for f in features])
            .value_counts()
            .rename_axis("Risk Level")
            .reset_index(name="Count")
        )
        st.bar_chart(breakdown.set_index("Risk Level"))


# ── Pipeline ──────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def run_validation(
    base: str,
    a_head: str, i_head: str, c_head: str,
    a_cyc: str,  i_cyc: str,
    a_reg: str,  i_reg: str,
):
    # 1. GitHub — one service per repo
    gh_android = GitHubService(
        Config.GITHUB_TOKEN, Config.GITHUB_REPO_ANDROID,
        platform="android", core_module_paths=Config.CORE_MODULE_PATHS_ANDROID,
    )
    gh_ios = GitHubService(
        Config.GITHUB_TOKEN, Config.GITHUB_REPO_IOS,
        platform="ios", core_module_paths=Config.CORE_MODULE_PATHS_IOS,
    )
    gh_config = GitHubService(
        Config.GITHUB_TOKEN, Config.GITHUB_REPO_CONFIG,
        platform="config", force_core_module=True,   # every config change is core
    )

    android_feats = gh_android.get_merged_features(base, a_head)
    ios_feats     = gh_ios.get_merged_features(base, i_head)
    config_feats  = gh_config.get_merged_features(base, c_head)

    # 2. Jira enrichment (all platforms together — one API call per Jira ID)
    jira = JiraService(Config.JIRA_URL, Config.JIRA_USER, Config.JIRA_TOKEN)
    all_feats = jira.enrich_features(android_feats + ios_feats + config_feats)

    android_feats = [f for f in all_feats if f.platform == "android"]
    ios_feats     = [f for f in all_feats if f.platform == "ios"]
    config_feats  = [f for f in all_feats if f.platform == "config"]

    # 3. QMetry — platform-specific cycles
    qm = QMetryService(Config.QMETRY_URL, Config.QMETRY_TOKEN)
    android_feats = qm.enrich_features(android_feats, cycle_id=a_cyc, regression_cycle_id=a_reg)
    ios_feats     = qm.enrich_features(ios_feats,     cycle_id=i_cyc, regression_cycle_id=i_reg)
    # Config features: checked against Android cycle as primary signal
    config_feats  = qm.enrich_features(config_feats,  cycle_id=a_cyc, regression_cycle_id=a_reg)

    # 4. Risk scoring
    return calculate_risk_for_all(android_feats + ios_feats + config_feats)


# ── Validation button ─────────────────────────────────────────────────────────
if st.button("▶️ Run Pre-Release Validation", type="primary", use_container_width=True):
    if not Config.GITHUB_TOKEN:
        st.error("GITHUB_TOKEN is not set. Please export it as an environment variable.")
        st.stop()

    with st.spinner("🔍 Fetching data from GitHub → Jira → QMetry …"):
        try:
            features = run_validation(
                base_branch,
                android_head, ios_head, config_head,
                android_cycle, ios_cycle,
                android_reg_cycle, ios_reg_cycle,
            )
            st.session_state["features"] = features
            st.session_state["validated"] = True
        except Exception as exc:
            st.error(f"Validation failed: {exc}")
            st.stop()

# ── Results ───────────────────────────────────────────────────────────────────
if st.session_state.get("validated"):
    all_features: list = st.session_state["features"]

    if not all_features:
        st.warning("No features with Jira IDs found across all three repositories.")
        st.stop()

    android_feats = [f for f in all_features if f.platform == "android"]
    ios_feats     = [f for f in all_features if f.platform == "ios"]
    config_feats  = [f for f in all_features if f.platform == "config"]

    # ── Cross-platform summary ─────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📊 Cross-Platform Summary")
    _metric_row(all_features)
    _verdict_banner(all_features)

    # Config-change alert
    if config_feats:
        st.warning(
            f"⚙️ **{len(config_feats)} config-repo change(s)** detected — "
            "these affect **both Android AND iOS**. Validate on both platforms."
        )

    # ── Per-platform tabs ──────────────────────────────────────────────
    st.markdown("---")
    tab_all, tab_android, tab_ios, tab_config = st.tabs([
        "📱 All Features",
        f"🤖 Android ({len(android_feats)})",
        f"🍎 iOS ({len(ios_feats)})",
        f"⚙️ Config ({len(config_feats)})",
    ])

    with tab_all:
        _verdict_banner(all_features)
        _feature_table(all_features)

    with tab_android:
        st.caption(f"`{Config.GITHUB_REPO_ANDROID}` · `{android_head}` → `{base_branch}`")
        _metric_row(android_feats)
        _verdict_banner(android_feats)
        _feature_table(android_feats)

    with tab_ios:
        st.caption(f"`{Config.GITHUB_REPO_IOS}` · `{ios_head}` → `{base_branch}`")
        _metric_row(ios_feats)
        _verdict_banner(ios_feats)
        _feature_table(ios_feats)

    with tab_config:
        st.caption(f"`{Config.GITHUB_REPO_CONFIG}` · `{config_head}` → `{base_branch}`")
        st.info("⚠️ Every config change is flagged **core_module = True** and affects **both platforms**.")
        _metric_row(config_feats)
        _verdict_banner(config_feats)
        _feature_table(config_feats)

else:
    st.info("👆 Click **Run Pre-Release Validation** to fetch and analyse the release features.")
