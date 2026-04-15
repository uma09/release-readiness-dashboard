"""
Peacock Mobile — Release Risk Engine  (Streamlit UI)

Sources
───────
  🤖 Android : NBCUDTC/gst-apps-android
  🍎 iOS     : NBCUDTC/gst-apps-ios
  ⚙️ Config  : NBCUDTC/peacock-mobile-config  (affects BOTH platforms)

Run with:
  streamlit run release_dashboard/app.py
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import streamlit as st
from github import Github, GithubException

from config import Config
from engine.governance_engine import calculate_risk_for_all
from models import Feature
from services.github_service import GitHubService
from services.jira_service import JiraService
from services.qmetry_service import QMetryService

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Peacock Mobile — Release Risk Engine",
    page_icon="🦚",
    layout="wide",
)

# ── Branch fetching (cached 5 min) ───────────────────────────────────────────
# Priority-sorted branch names shown at the top of every dropdown.
_PRIORITY_BRANCHES = ["main", "master", "develop", "release", "staging"]


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_repo_branches(token: str, repo: str) -> list[str]:
    """Return sorted branch names for *repo*. Empty list on auth/network error."""
    if not token:
        return []
    try:
        names = sorted(b.name for b in Github(token).get_repo(repo).get_branches())
        top  = [b for b in _PRIORITY_BRANCHES if b in names]
        rest = [b for b in names if b not in _PRIORITY_BRANCHES]
        return top + rest
    except GithubException as exc:
        st.sidebar.warning(f"Could not load branches for `{repo}`: {exc.data.get('message', exc)}")
        return []
    except Exception as exc:
        st.sidebar.warning(f"Branch fetch error for `{repo}`: {exc}")
        return []


def _branch_select(label: str, branches: list[str], default: str) -> str:
    """Always renders a st.selectbox.

    When *branches* is populated (GITHUB_TOKEN set) → full live branch list.
    When *branches* is empty (token missing / API error) → selectbox with just
    the configured default so the UI always shows a dropdown, never a text box.
    """
    options = branches if branches else [default]
    idx = options.index(default) if default in options else 0
    return st.selectbox(label, options, index=idx)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/9/9f/Peacock_tv_logo.svg/320px-Peacock_tv_logo.svg.png", width=140)
    st.title("⚙️ Configuration")

    st.subheader("🌿 Branches")

    # Fetch branches per repo (cached 5 min)
    if not Config.GITHUB_TOKEN:
        st.warning("⚠️ Set **GITHUB_TOKEN** to load live branch lists from GitHub.")
    with st.spinner("Loading branches …"):
        _branches_android = _fetch_repo_branches(Config.GITHUB_TOKEN, Config.GITHUB_REPO_ANDROID)
        _branches_ios     = _fetch_repo_branches(Config.GITHUB_TOKEN, Config.GITHUB_REPO_IOS)
        _branches_config  = _fetch_repo_branches(Config.GITHUB_TOKEN, Config.GITHUB_REPO_CONFIG)

    # Release branch — union of all three repos so any release/x.x branch is visible
    _all_branches = list(dict.fromkeys(
        [b for b in _PRIORITY_BRANCHES
         if b in _branches_android or b in _branches_ios or b in _branches_config]
        + sorted({*_branches_android, *_branches_ios, *_branches_config}
                 - set(_PRIORITY_BRANCHES))
    ))

    st.caption("Diff = develop → release branch (what's shipping)")
    release_branch   = _branch_select("🚀 Release branch",    _all_branches,     Config.GITHUB_BASE_BRANCH)
    android_develop  = _branch_select("🤖 Android develop",   _branches_android, Config.GITHUB_HEAD_BRANCH_ANDROID)
    ios_develop      = _branch_select("🍎 iOS develop",        _branches_ios,     Config.GITHUB_HEAD_BRANCH_IOS)
    config_develop   = _branch_select("⚙️ Config develop",   _branches_config,  Config.GITHUB_HEAD_BRANCH_CONFIG)

    st.subheader("🧪 QMetry Test Cycles")
    android_cycle     = st.text_input("Android cycle ID",          value=Config.QMETRY_CYCLE_ID_ANDROID)
    ios_cycle         = st.text_input("iOS cycle ID",              value=Config.QMETRY_CYCLE_ID_IOS)
    android_reg_cycle = st.text_input("Android regression cycle",  value=Config.QMETRY_REGRESSION_CYCLE_ID_ANDROID)
    ios_reg_cycle     = st.text_input("iOS regression cycle",      value=Config.QMETRY_REGRESSION_CYCLE_ID_IOS)

    st.markdown("---")
    st.caption("🔑 Credentials are loaded from environment variables.")

# ── Title ─────────────────────────────────────────────────────────────────────
st.title("🦚 Peacock Mobile — Release Risk Engine")
st.caption(
    f"🚀 Release: **`{release_branch}`** ← develop: "
    f"🤖 `{android_develop}` · 🍎 `{ios_develop}` · ⚙️ `{config_develop}`"
)

# ── Constants & helpers ───────────────────────────────────────────────────────
_RISK_COLOURS = {
    "LOW":      "background-color: #d4edda; color: #155724",
    "MEDIUM":   "background-color: #fff3cd; color: #856404",
    "HIGH":     "background-color: #f8d7da; color: #721c24",
    "CRITICAL": "background-color: #6f42c1; color: #ffffff",
    "UNKNOWN":  "background-color: #e2e3e5; color: #383d41",
}
_RISK_EMOJI  = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴", "CRITICAL": "🟣", "UNKNOWN": "⚪"}
_PLATFORM_ICON = {"android": "🤖", "ios": "🍎", "config": "⚙️"}
_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

_TABLE_COLS = [
    "platform", "jira_id", "pr_number",
    "jira_summary", "jira_status", "jira_sprint",
    "core_module", "affects_both_platforms",
    "code_risk_score", "test_risk_score", "jira_risk_score",
    "risk_score", "risk_level",
    "test_case_exists", "executed", "in_regression",
]


def _style_row(row: pd.Series):
    colour = _RISK_COLOURS.get(str(row.get("risk_level", "UNKNOWN")).upper(), "")
    return [colour] * len(row)


def features_to_df(features: list) -> pd.DataFrame:
    rows = []
    for f in features:
        rows.append({col: getattr(f, col, "") for col in _TABLE_COLS})
    df = pd.DataFrame(rows, columns=_TABLE_COLS)
    df["platform"] = df["platform"].map(lambda p: f"{_PLATFORM_ICON.get(p, '')} {p}")
    return df


def _to_csv(features: list) -> bytes:
    return features_to_df(features).to_csv(index=False).encode()


def _metric_row(features: list):
    total    = len(features)
    critical = sum(1 for f in features if f.risk_level == "CRITICAL")
    high     = sum(1 for f in features if f.risk_level == "HIGH")
    no_test  = sum(1 for f in features if not f.test_case_exists)
    not_run  = sum(1 for f in features if not f.executed)
    in_reg   = sum(1 for f in features if f.in_regression)
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Features",         total)
    c2.metric("🟣 CRITICAL",      critical)
    c3.metric("🔴 HIGH",          high)
    c4.metric("⚠️ No Test Case",  no_test)
    c5.metric("⏸️ Not Executed",  not_run)
    c6.metric("✅ In Regression", in_reg)


def _verdict_banner(features: list):
    high_risk = sum(1 for f in features if f.risk_level in ("HIGH", "CRITICAL"))
    if high_risk == 0:
        st.success("✅ **GO** — No HIGH or CRITICAL risk features detected.")
    elif high_risk <= 2:
        st.warning(f"⚠️ **CONDITIONAL** — {high_risk} feature(s) are HIGH/CRITICAL. Review before shipping.")
    else:
        st.error(f"🚫 **NO-GO** — {high_risk} features are HIGH or CRITICAL risk. Fix test gaps first.")


def _risk_heatmap(features: list):
    """Platform × Risk Level count matrix with background colour."""
    platforms = ["android", "ios", "config"]
    data = {lvl: [] for lvl in _LEVELS}
    data["Platform"] = [f"{_PLATFORM_ICON.get(p,'')} {p}" for p in platforms]
    for lvl in _LEVELS:
        for p in platforms:
            data[lvl].append(sum(1 for f in features if f.platform == p and f.risk_level == lvl))
    df = pd.DataFrame(data).set_index("Platform")

    def _colour_cell(val):
        if val == 0:
            return "background-color: #f8f9fa; color: #6c757d"
        col_map = {"LOW": "#d4edda", "MEDIUM": "#fff3cd", "HIGH": "#f8d7da", "CRITICAL": "#e2c4f7"}
        # We can't get column name easily in applymap, use value intensity
        return "font-weight: bold"

    st.dataframe(
        df.style.background_gradient(cmap="RdYlGn_r", axis=None).format("{}"),
        use_container_width=True,
    )


def _drill_down(f: Feature):
    """Render the 3-panel drill-down for a single Feature inside an expander."""
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**📂 PR Diff**")
        if f.pr_diff:
            d = f.pr_diff
            st.write(f"➕ {d.lines_added} / ➖ {d.lines_removed} lines")
            st.write(f"Files: {d.total_files} (src {d.source_files} · test {d.test_files} · cfg {d.config_files})")
            st.write(f"{'✅' if d.has_test_changes else '❌'} Test files changed")
            st.write(f"⏱️ PR open {d.days_open} day(s) · 👀 {d.review_count} review(s)")
        else:
            st.caption("No diff data available")

    with col2:
        st.markdown("**🎫 Jira**")
        if f.jira_meta:
            m = f.jira_meta
            st.write(f"**{m.summary[:80]}**" if m.summary else "—")
            st.write(f"Status: `{m.status}` · Priority: `{m.priority}`")
            st.write(f"Type: `{m.issue_type}` · SP: `{m.story_points or '—'}`")
            st.write(f"Sprint: `{m.sprint or '—'}` · Fix: `{m.fix_version or '—'}`")
            if m.labels:
                st.write("Labels: " + ", ".join(f"`{l}`" for l in m.labels))
        else:
            st.caption("No Jira metadata available")

    with col3:
        st.markdown("**🧪 QMetry**")
        if f.test_exec:
            te = f.test_exec
            st.write(f"Total: {te.total_cases} · ✅ {te.passed} · ❌ {te.failed} · 🚧 {te.blocked} · ⏸️ {te.not_run}")
            st.write(f"Pass rate: **{te.pass_rate}%**")
            st.write(f"{'✅' if te.in_regression else '❌'} In regression suite")
        else:
            st.caption("No test execution data available")

        st.markdown("---")
        st.write(f"Code risk: `{f.code_risk_score}/10`")
        st.write(f"Test risk: `{f.test_risk_score}/10`")
        st.write(f"Jira risk: `{f.jira_risk_score}/10`")
        st.write(f"**Overall: `{f.risk_score}/10` — {_RISK_EMOJI.get(f.risk_level,'')} {f.risk_level}**")


def _platform_panel(features: list, platform: str, repo: str, head: str):
    """Render the full panel for one platform tab."""
    if not features:
        st.info(f"No features with Jira IDs found in `{repo}` between `{head}` → `{base_branch}`.")
        return

    st.caption(f"`{repo}` · `{head}` → `{base_branch}`")
    _metric_row(features)
    _verdict_banner(features)

    # Colour-coded table
    st.markdown("#### 🗂️ Feature Table")
    df = features_to_df(features)
    st.dataframe(df.style.apply(_style_row, axis=1), use_container_width=True, height=380)

    # CSV export
    st.download_button(
        label=f"⬇️ Export {platform.upper()} CSV",
        data=_to_csv(features),
        file_name=f"risk_{platform}.csv",
        mime="text/csv",
    )

    # Per-feature drill-down
    st.markdown("#### 🔍 Feature Drill-Down")
    for f in sorted(features, key=lambda x: x.risk_score, reverse=True):
        icon  = _RISK_EMOJI.get(f.risk_level, "⚪")
        label = f"{icon} [{f.jira_id}] {(f.jira_summary or 'PR #'+str(f.pr_number))[:70]}  · overall {f.risk_score}/10"
        with st.expander(label):
            _drill_down(f)


# ── Pipeline ──────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def run_validation(
    release: str,
    a_dev: str, i_dev: str, c_dev: str,
    a_cyc: str, i_cyc: str,
    a_reg: str, i_reg: str,
):
    """Compare develop → release branch for each repo.

    PyGithub compare(base, head) returns commits in *head* not in *base*.
    base = develop branch  →  head = release branch
    Result: every commit that is in the release but not yet in develop
            (hotfixes) PLUS all PRs whose commits landed on the release branch.
    """
    gh_android = GitHubService(Config.GITHUB_TOKEN, Config.GITHUB_REPO_ANDROID,
                               platform="android", core_module_paths=Config.CORE_MODULE_PATHS_ANDROID)
    gh_ios     = GitHubService(Config.GITHUB_TOKEN, Config.GITHUB_REPO_IOS,
                               platform="ios",     core_module_paths=Config.CORE_MODULE_PATHS_IOS)
    gh_config  = GitHubService(Config.GITHUB_TOKEN, Config.GITHUB_REPO_CONFIG,
                               platform="config",  force_core_module=True)

    # base = develop, head = release  →  diff shows what's in the release
    android_feats = gh_android.get_merged_features(base_branch=a_dev, head_branch=release)
    ios_feats     = gh_ios.get_merged_features(base_branch=i_dev,     head_branch=release)
    config_feats  = gh_config.get_merged_features(base_branch=c_dev,  head_branch=release)

    # 2. Jira — full metadata (JiraMetadata) per ticket
    jira      = JiraService(Config.JIRA_URL, Config.JIRA_USER, Config.JIRA_TOKEN)
    all_feats = jira.enrich_features(android_feats + ios_feats + config_feats)

    android_feats = [f for f in all_feats if f.platform == "android"]
    ios_feats     = [f for f in all_feats if f.platform == "ios"]
    config_feats  = [f for f in all_feats if f.platform == "config"]

    # 3. QMetry — TestExecution counts per platform cycle
    qm            = QMetryService(Config.QMETRY_URL, Config.QMETRY_TOKEN)
    android_feats = qm.enrich_features(android_feats, cycle_id=a_cyc, regression_cycle_id=a_reg)
    ios_feats     = qm.enrich_features(ios_feats,     cycle_id=i_cyc, regression_cycle_id=i_reg)
    config_feats  = qm.enrich_features(config_feats,  cycle_id=a_cyc, regression_cycle_id=a_reg)

    # 4. Governance — 3-dimension risk scoring
    return calculate_risk_for_all(android_feats + ios_feats + config_feats)


# ── Run button ────────────────────────────────────────────────────────────────
if st.button("▶️ Run Release Risk Analysis", type="primary", use_container_width=True):
    if not Config.GITHUB_TOKEN:
        st.error("GITHUB_TOKEN is not set. Export it as an environment variable and restart.")
        st.stop()

    with st.spinner("🔍 GitHub → Jira → QMetry → Governance Engine …"):
        try:
            features = run_validation(
                release_branch,
                android_develop, ios_develop, config_develop,
                android_cycle, ios_cycle,
                android_reg_cycle, ios_reg_cycle,
            )
            st.session_state["features"]  = features
            st.session_state["validated"] = True
        except Exception as exc:
            st.error(f"Analysis failed: {exc}")
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

    # ── Executive summary ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📊 Executive Summary")
    _metric_row(all_features)
    _verdict_banner(all_features)
    if config_feats:
        st.warning(
            f"⚙️ **{len(config_feats)} config-repo change(s)** — affect **both Android & iOS**."
        )

    # ── Risk heatmap ───────────────────────────────────────────────────
    with st.expander("🌡️ Risk Heatmap — Platform × Risk Level", expanded=True):
        _risk_heatmap(all_features)

    # ── Full CSV export ────────────────────────────────────────────────
    st.download_button(
        label="⬇️ Export All Features (CSV)",
        data=_to_csv(all_features),
        file_name="release_risk_all.csv",
        mime="text/csv",
    )

    # ── Per-platform tabs ──────────────────────────────────────────────
    st.markdown("---")
    tab_all, tab_android, tab_ios, tab_config = st.tabs([
        f"📱 All ({len(all_features)})",
        f"🤖 Android ({len(android_feats)})",
        f"🍎 iOS ({len(ios_feats)})",
        f"⚙️ Config ({len(config_feats)})",
    ])

    with tab_all:
        _verdict_banner(all_features)
        df_all = features_to_df(all_features)
        st.dataframe(df_all.style.apply(_style_row, axis=1), use_container_width=True, height=400)
        st.markdown("#### 🔍 Feature Drill-Down")
        for f in sorted(all_features, key=lambda x: x.risk_score, reverse=True):
            icon  = _RISK_EMOJI.get(f.risk_level, "⚪")
            plat  = _PLATFORM_ICON.get(f.platform, "")
            label = f"{icon} {plat} [{f.jira_id}] {(f.jira_summary or 'PR #'+str(f.pr_number))[:65]}  · {f.risk_score}/10"
            with st.expander(label):
                _drill_down(f)

    with tab_android:
        _platform_panel(android_feats, "android", Config.GITHUB_REPO_ANDROID, android_develop)

    with tab_ios:
        _platform_panel(ios_feats, "ios", Config.GITHUB_REPO_IOS, ios_develop)

    with tab_config:
        st.info("⚠️ Every config change has `core_module = True` and `affects_both_platforms = True`.")
        _platform_panel(config_feats, "config", Config.GITHUB_REPO_CONFIG, config_develop)

else:
    st.info("👆 Click **Run Release Risk Analysis** to fetch and score your release features.")
