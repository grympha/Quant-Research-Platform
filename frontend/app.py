"""
XAUUSD Quant Research Platform — Streamlit Dashboard v3.1
==========================================================
Tab 1: Run Analysis  (date range · Full / Yearly / Monthly / Walk Forward)
Tab 2: OHLCV Dataset Library
Tab 3: Research History
Tab 4: Export Center
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta, timezone
from pathlib import Path

import os
import sys
from pathlib import Path as _Path

# Make frontend/ importable regardless of cwd
sys.path.insert(0, str(_Path(__file__).parent))
import services as svc  # noqa: E402

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# API_BASE_URL is consumed by services.py; kept here only for legacy reference.
API = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")

GOAL_MR_MIN = 3.0
GOAL_MR_MAX = 5.0
GOAL_DD_LIM = 4.0
GOAL_PF_MIN = 1.5

TF_ALL   = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
TF_ORDER = {tf: i for i, tf in enumerate(TF_ALL)}

_STATUS_CLASS = {
    "PASS":              "badge-pass",
    "WATCHLIST":         "badge-watchlist",
    "FAIL":              "badge-fail",
    "INSUFFICIENT DATA": "badge-insuf",
}

_SUB_MODE_LABELS = {
    "full_backtest": "Full Backtest",
    "yearly":        "Yearly Analysis",
    "monthly":       "Monthly Analysis",
    "walk_forward":  "Walk Forward",
}

_SUB_MODE_API = {
    "Full Backtest":          "full_backtest",
    "Yearly Analysis":        "yearly",
    "Monthly Analysis":       "monthly",
    "Walk Forward Analysis":  "walk_forward",
}

_MYT = timezone(timedelta(hours=8))   # Malaysia Time = UTC+8, no DST

_MODULE_LABELS: dict[str, str] = {
    "liquidity_sweep": "Liquidity Sweep",
    "break_retest":    "Break & Retest",
}


def _to_myt(utc_str: str | None) -> str:
    """Convert a UTC ISO datetime string → DD-MM-YYYY HH:mm MYT."""
    if not utc_str:
        return "—"
    try:
        ts = pd.Timestamp(utc_str)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        ts_myt = ts.tz_convert(_MYT)
        return ts_myt.strftime("%d-%m-%Y %H:%M") + " MYT"
    except Exception:
        return str(utc_str)[:16].replace("T", " ")


def _module_label(module_id: str | None) -> str:
    """Human-readable module name; falls back gracefully for old records."""
    if not module_id:
        return "Unknown / Legacy Run"
    return _MODULE_LABELS.get(module_id, module_id.replace("_", " ").title())


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Quant Research Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.badge { padding:4px 16px; border-radius:20px; font-weight:700;
         font-size:1.05rem; display:inline-block; }
.badge-pass      { background:#1b5e20; color:#a5d6a7; }
.badge-watchlist { background:#e65100; color:#ffe0b2; }
.badge-fail      { background:#b71c1c; color:#ef9a9a; }
.badge-insuf     { background:#37474f; color:#b0bec5; }
.goal-pass  { border-left:4px solid #26a69a; padding-left:10px; }
.goal-watch { border-left:4px solid #ffa726; padding-left:10px; }
.goal-fail  { border-left:4px solid #ef5350; padding-left:10px; }
.range-chip { background:#1e3a5f; color:#90caf9; padding:3px 12px;
              border-radius:12px; font-size:0.85rem; font-weight:600;
              display:inline-block; margin:2px 4px 2px 0; }
.ds-chip    { background:#1a2f1a; color:#81c784; padding:3px 12px;
              border-radius:12px; font-size:0.85rem; font-weight:600;
              display:inline-block; margin:2px 4px 2px 0; }
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
col_t, col_s = st.columns([3, 1])
with col_t:
    st.title("📊 Quant Research Platform")
    st.caption("v3.2 — Phase 1.5 Stability · Platform Health · Reset DB")
with col_s:
    if svc.RUN_MODE == "streamlit_only":
        st.success("Local Engine Active  🟢", icon=None)
    else:
        try:
            _hi = svc.health()
            st.success(f"API v{_hi.get('version','?')}  🟢", icon=None)
        except Exception:
            st.error("API offline — run uvicorn", icon="🔴")

# ── Goal profile loader (defined early — called at sidebar level before helpers) ──

@st.cache_data(ttl=30, show_spinner=False)
def _fetch_goal_profiles() -> list[dict]:
    return svc.list_goal_profiles()


# ── Sidebar ───────────────────────────────────────────────────────────────────
_sidebar_profiles = _fetch_goal_profiles()
_sidebar_pids     = [p["id"] for p in _sidebar_profiles]
_sidebar_default  = next((p for p in _sidebar_profiles if p.get("is_default")), _sidebar_profiles[0] if _sidebar_profiles else None)

with st.sidebar:
    st.header("⚙️ Strategy Parameters")

    # Performance mode toggle — outside form so it reacts immediately
    perf_mode = st.toggle(
        "⚡ Fast Mode",
        value=st.session_state.get("perf_mode", False),
        help="Hides heavy charts for faster rendering. Disable to see full equity/drawdown charts.",
    )
    st.session_state["perf_mode"] = perf_mode

    st.divider()

    # Strategy params form — changes don't trigger reruns until Apply is clicked
    with st.form("strategy_params", border=False):
        st.markdown("**Module**")
        module = st.selectbox(
            "module",
            ["liquidity_sweep", "break_retest"],
            format_func=lambda x: _MODULE_LABELS.get(x, x),
            label_visibility="collapsed",
        )

        st.markdown("**Risk per Trade**")
        risk_pct = st.slider("risk", 0.25, 3.0, 1.0, 0.25,
                             format="%.2f%%", label_visibility="collapsed")

        st.markdown("**Risk : Reward**")
        rr = st.slider("rr", 1.0, 5.0, 2.0, 0.5,
                       format="%.1fR", label_visibility="collapsed")

        st.markdown("**Swing Strength (N bars each side)**")
        lookback = st.slider(
            "lookback", 2, 20, 5, 1,
            label_visibility="collapsed",
            help="Higher = only major pivots; lower = more sensitive swings.",
        )

        st.divider()
        st.markdown("**Break & Retest Params**")
        st.caption("Applied only when Break & Retest module is selected.")
        breakout_buffer = st.slider(
            "Breakout Buffer",
            min_value=0.0, max_value=2.0, value=0.10, step=0.05,
            key="breakout_buffer",
            help="Minimum distance the close must exceed the swing level to register a breakout.",
        )
        retest_tolerance = st.slider(
            "Retest Tolerance",
            min_value=0.0, max_value=2.0, value=0.50, step=0.05,
            key="retest_tolerance",
            help="How close the retest wick must get to the broken level.",
        )

        st.divider()
        st.markdown("**🎯 Goal Configuration**")
        if _sidebar_pids:
            _def_idx = _sidebar_pids.index(_sidebar_default["id"]) if _sidebar_default else 0
            goal_profile_id = st.selectbox(
                "goal_profile",
                _sidebar_pids,
                index=_def_idx,
                format_func=lambda pid: next(
                    (p["profile_name"] for p in _sidebar_profiles if p["id"] == pid),
                    "Unknown",
                ),
                label_visibility="collapsed",
                key="sidebar_goal_profile",
            )
        else:
            goal_profile_id = None
            st.caption("No profiles found — backend may be offline.")

        st.form_submit_button("✅ Apply Settings", use_container_width=True, type="primary")

    # Dynamic goal targets display based on currently selected profile
    _sel_prof = next((p for p in _sidebar_profiles if p.get("id") == goal_profile_id), _sidebar_default)
    st.divider()
    if _sel_prof:
        st.markdown(f"**🎯 {_sel_prof['profile_name']}**")
        st.markdown(f"- Monthly Return ≥ **{_sel_prof['monthly_return_target']}%**")
        st.markdown(f"- Daily DD ≤ **{_sel_prof['daily_drawdown_target']}%**")
        st.markdown(f"- Monthly DD ≤ **{_sel_prof['monthly_drawdown_target']}%**")
        st.markdown(f"- Profit Factor ≥ **{_sel_prof['profit_factor_target']}**")
        st.caption("Manage profiles in **🎯 Goal Profiles** tab.")
    else:
        st.markdown("**🎯 Target Goals**")
        st.caption("No goal profile loaded. Check backend connection.")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _detect_tf(filename: str) -> str | None:
    stem = re.sub(r"[\s\-\.]", "_", Path(filename).stem.upper())
    for tf in sorted(TF_ALL, key=len, reverse=True):
        if re.search(r"(?:^|_)" + re.escape(tf) + r"(?:_|$)", stem):
            return tf
    return None


def _sorted_tfs(tfs: list[str]) -> list[str]:
    return sorted(tfs, key=lambda t: TF_ORDER.get(t, 99))


def _dataset_label(d: dict) -> str:
    return (
        f"{d['timeframe']} — {d['filename']} "
        f"({d['total_rows']:,} rows · {d['start_datetime'][:10]} → {d['end_datetime'][:10]})"
    )


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_datasets() -> list[dict]:
    t0 = time.perf_counter()
    result = svc.list_datasets()
    print(f"[perf] _fetch_datasets cache-miss: {(time.perf_counter()-t0)*1000:.0f}ms  ({len(result)} datasets)", flush=True)
    return result


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_research_runs(limit: int = 50) -> list[dict]:
    t0 = time.perf_counter()
    result = svc.list_research_runs(limit)
    print(f"[perf] _fetch_research_runs cache-miss: {(time.perf_counter()-t0)*1000:.0f}ms  ({len(result)} runs)", flush=True)
    return result


def _clear_data_cache() -> None:
    """Invalidate cached fetch results after any mutation (upload/delete/reset/analysis)."""
    _fetch_datasets.clear()
    _fetch_research_runs.clear()
    _fetch_goal_profiles.clear()


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return pd.Timestamp(s).date()
    except Exception:
        return None


pos_col = "#26a69a"
neg_col = "#ef5350"


# ── Results renderer (shared across Tab 1, Tab 3) ─────────────────────────────

def _render_results(result: dict, risk_pct_val: float, label: str = "", chart_key_prefix: str = "main") -> None:
    fast_mode = st.session_state.get("perf_mode", False)
    rpt      = result.get("report", {})
    trades   = result.get("trades", [])
    params   = result.get("parameters", {})
    bt_start = result.get("backtest_start")
    bt_end   = result.get("backtest_end")
    sub_mode = result.get("analysis_sub_mode", "full_backtest")
    tfs_used = result.get("timeframes_used", [result.get("timeframe", "—")])

    if fast_mode:
        st.caption("⚡ Fast Mode — charts hidden. Disable in sidebar to view full charts.")

    if label:
        st.markdown(f"#### {label}")

    # Multi-TF banner
    if result.get("analysis_mode") == "multi":
        parts = [f"Structure: **{result.get('structure_tf', '—')}**"]
        if result.get("trend_tf"):
            parts.insert(0, f"Trend: **{result['trend_tf']}**")
        if result.get("entry_tf"):
            parts.append(f"Entry: **{result['entry_tf']}**")
        st.info("Multi-TF — " + " | ".join(parts))

    # Info chips: analysis range · mode · timeframes
    chips: list[str] = []
    if bt_start or bt_end:
        chips.append(
            f'<span class="range-chip">📅 {bt_start or "—"} → {bt_end or "—"}</span>'
        )
    chips.append(
        f'<span class="range-chip">🔬 {_SUB_MODE_LABELS.get(sub_mode, sub_mode)}</span>'
    )
    chips.append(
        f'<span class="ds-chip">📊 {", ".join(_sorted_tfs(tfs_used))}</span>'
    )
    st.markdown(" ".join(chips), unsafe_allow_html=True)
    st.write("")

    # Goal badge
    status = rpt.get("goal_status", "INSUFFICIENT DATA")
    bc     = _STATUS_CLASS.get(status, "badge-insuf")
    st.markdown(
        f"**Goal Status:** &nbsp;<span class='badge {bc}'>{status}</span>",
        unsafe_allow_html=True,
    )
    st.write("")

    # Derive goal thresholds from stored report (works for both old and new format)
    _goals_cfg     = rpt.get("goals", {})
    _is_new_goals  = "monthly_return_target" in _goals_cfg
    _mr_target     = _goals_cfg.get("monthly_return_target", GOAL_MR_MIN)
    _dd_target     = _goals_cfg.get("daily_drawdown_target", GOAL_DD_LIM)

    # Key metrics row
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Trades",   rpt.get("total_trades", 0))
    c2.metric("Win Rate", f"{rpt.get('win_rate', 0):.1f}%")
    c3.metric("PF",       f"{rpt.get('profit_factor', 0):.2f}")
    c4.metric("Net R",    f"{rpt.get('net_r', 0):+.2f}R")
    _mr = rpt.get("monthly_return", 0)
    _mr_ok = _mr >= _mr_target if _is_new_goals else GOAL_MR_MIN <= _mr <= GOAL_MR_MAX
    c5.metric("Monthly", f"{_mr:.2f}%",
              delta="On target" if _mr_ok else "Off target",
              delta_color="normal" if _mr_ok else "inverse")
    _dd = rpt.get("max_drawdown", 0)
    _dd_ok = _dd <= _dd_target if _is_new_goals else _dd < GOAL_DD_LIM
    c6.metric("Max DD", f"{_dd:.2f}%",
              delta="OK" if _dd_ok else "Exceeded",
              delta_color="normal" if _dd_ok else "inverse")
    c7.metric("Open/Exp", rpt.get("open_trades", 0))
    st.write("")

    teq, tdd, tmo, ttr, tgo = st.tabs([
        "📈 Equity Curve", "📉 Drawdown", "📅 Monthly", "📋 Trades", "🎯 Goals",
    ])

    # ── Equity Curve ──────────────────────────────────────────────────────────
    with teq:
        if fast_mode:
            st.info("Chart hidden in Fast Mode. Disable ⚡ Fast Mode in sidebar to view.")
        else:
            equity = rpt.get("equity_curve", [0.0])
            lc = pos_col if equity[-1] >= 0 else neg_col
            fc = "rgba(38,166,154,0.10)" if equity[-1] >= 0 else "rgba(239,83,80,0.10)"
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=list(range(len(equity))), y=equity,
                mode="lines", line=dict(color=lc, width=2),
                fill="tozeroy", fillcolor=fc,
            ))
            fig.add_hline(y=0, line_color="rgba(255,255,255,0.25)", line_dash="dash")
            fig.update_layout(
                title=f"Cumulative Return — {rpt.get('total_trades', 0)} closed trades",
                xaxis_title="Trade #", yaxis_title="Return (%)",
                template="plotly_dark", height=350,
                margin=dict(l=10, r=10, t=45, b=10), showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True, key=f"{chart_key_prefix}_equity")

            cd, ct = st.columns([1, 2])
            with cd:
                fig_p = go.Figure(go.Pie(
                    labels=["Win", "Loss", "Open"],
                    values=[rpt.get("win_trades", 0), rpt.get("loss_trades", 0), rpt.get("open_trades", 0)],
                    marker_colors=[pos_col, neg_col, "#ffa726"],
                    hole=0.45, textinfo="label+percent",
                ))
                fig_p.update_layout(template="plotly_dark", height=240,
                                    margin=dict(l=0, r=0, t=20, b=0), showlegend=False)
                st.plotly_chart(fig_p, use_container_width=True, key=f"{chart_key_prefix}_pie")
            with ct:
                st.dataframe(pd.DataFrame([
                    ("Gross Profit R", f"+{rpt.get('gross_profit_r', 0):.2f}R"),
                    ("Gross Loss R",   f"-{rpt.get('gross_loss_r', 0):.2f}R"),
                    ("Net R",          f"{rpt.get('net_r', 0):+.2f}R"),
                    ("Net Return",     f"{rpt.get('net_r', 0) * risk_pct_val:.2f}%"),
                    ("Avg Monthly",    f"{rpt.get('monthly_return', 0):.2f}%"),
                    ("Max Drawdown",   f"{rpt.get('max_drawdown', 0):.2f}%"),
                    ("Profit Factor",  f"{rpt.get('profit_factor', 0):.2f}"),
                    ("Win Rate",       f"{rpt.get('win_rate', 0):.1f}%"),
                ], columns=["Metric", "Value"]), use_container_width=True, hide_index=True)

    # ── Drawdown Curve ────────────────────────────────────────────────────────
    with tdd:
        _dd2    = rpt.get("max_drawdown", 0)
        _dd_lim = _goals_cfg.get("daily_drawdown_target", GOAL_DD_LIM)
        st.metric(
            "Max Drawdown",
            f"{_dd2:.2f}%",
            delta="Within limit" if _dd2 <= _dd_lim else "Exceeded limit",
            delta_color="normal" if _dd2 <= _dd_lim else "inverse",
        )
        if fast_mode:
            st.info("Drawdown chart hidden in Fast Mode. Disable ⚡ Fast Mode in sidebar to view.")
        else:
            dd_curve = rpt.get("drawdown_curve", [])
            if dd_curve:
                fig_dd = go.Figure()
                fig_dd.add_trace(go.Scatter(
                    x=list(range(len(dd_curve))),
                    y=[-v for v in dd_curve],
                    mode="lines",
                    line=dict(color=neg_col, width=1.5),
                    fill="tozeroy",
                    fillcolor="rgba(239,83,80,0.12)",
                ))
                fig_dd.add_hline(
                    y=-_dd_lim,
                    line_color="rgba(255,167,38,0.7)",
                    line_dash="dash",
                    annotation_text=f"Limit {_dd_lim}%",
                    annotation_position="top right",
                )
                fig_dd.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_dash="dot")
                fig_dd.update_layout(
                    title=f"Drawdown Curve — Max {rpt.get('max_drawdown', 0):.2f}%",
                    xaxis_title="Trade #",
                    yaxis_title="Drawdown (%)",
                    template="plotly_dark",
                    height=380,
                    margin=dict(l=10, r=10, t=45, b=10),
                    showlegend=False,
                )
                st.plotly_chart(fig_dd, use_container_width=True, key=f"{chart_key_prefix}_dd")
            else:
                st.info("Drawdown curve not available — run a new analysis to generate it.")

    # ── Monthly Return ────────────────────────────────────────────────────────
    with tmo:
        monthly = rpt.get("monthly_breakdown", [])
        if not monthly:
            st.info("No closed trades to build monthly breakdown.")
        else:
            months = [m["month"] for m in monthly]
            retpct = [m["return_pct"] for m in monthly]
            fig_mo = go.Figure()
            fig_mo.add_trace(go.Bar(
                x=months, y=retpct,
                marker_color=[pos_col if v >= 0 else neg_col for v in retpct],
                text=[f"{v:+.1f}%" for v in retpct], textposition="outside",
            ))
            _mr_tgt = _goals_cfg.get("monthly_return_target", GOAL_MR_MIN)
            if _is_new_goals:
                fig_mo.add_hline(y=_mr_tgt, line_color="rgba(38,166,154,0.7)",
                                 line_dash="dash",
                                 annotation_text=f"Target ≥{_mr_tgt}%",
                                 annotation_position="top right")
            else:
                fig_mo.add_hrect(y0=GOAL_MR_MIN, y1=GOAL_MR_MAX,
                                 fillcolor="rgba(38,166,154,0.08)", line_width=0,
                                 annotation_text="Target 3–5%", annotation_position="top right")
            fig_mo.add_hline(y=0, line_color="rgba(255,255,255,0.25)", line_dash="dash")
            fig_mo.update_layout(template="plotly_dark", height=350,
                                 margin=dict(l=10, r=10, t=45, b=10))
            st.plotly_chart(fig_mo, use_container_width=True, key=f"{chart_key_prefix}_monthly")

            # Yearly breakdown (shown when sub_mode == "yearly")
            if sub_mode == "yearly":
                st.divider()
                st.markdown("**Year-by-Year Summary**")

                year_data: dict[str, dict] = {}
                for m in monthly:
                    yr = m["month"][:4]
                    if yr not in year_data:
                        year_data[yr] = {
                            "trades": 0, "wins": 0, "losses": 0,
                            "net_r": 0.0, "return_pct": 0.0, "months": 0,
                        }
                    d2 = year_data[yr]
                    d2["trades"]     += m["trades"]
                    d2["wins"]       += m["wins"]
                    d2["losses"]     += m["losses"]
                    d2["net_r"]      += m["net_r"]
                    d2["return_pct"] += m["return_pct"]
                    d2["months"]     += 1

                year_rows = []
                for yr, d2 in sorted(year_data.items()):
                    avg_mo = d2["return_pct"] / d2["months"] if d2["months"] > 0 else 0.0
                    wr2    = d2["wins"] / d2["trades"] * 100 if d2["trades"] > 0 else 0.0
                    _yr_tgt = _goals_cfg.get("monthly_return_target", GOAL_MR_MIN)
                    _yr_ok  = avg_mo >= _yr_tgt if _is_new_goals else GOAL_MR_MIN <= avg_mo <= GOAL_MR_MAX
                    year_rows.append({
                        "Year":    yr,
                        "Months":  d2["months"],
                        "Trades":  d2["trades"],
                        "Win%":    f"{wr2:.1f}",
                        "Net R":   f"{d2['net_r']:+.2f}",
                        "Total %": f"{d2['return_pct']:+.2f}",
                        "Avg Mo%": f"{avg_mo:+.2f}",
                        "Target":  "✅" if _yr_ok else "❌",
                    })

                st.dataframe(pd.DataFrame(year_rows), use_container_width=True, hide_index=True)

                yr_vals = [float(r["Avg Mo%"]) for r in year_rows]
                fig_yr = go.Figure(go.Bar(
                    x=[r["Year"] for r in year_rows],
                    y=yr_vals,
                    marker_color=[pos_col if v >= 0 else neg_col for v in yr_vals],
                    text=[f"{v:+.2f}%" for v in yr_vals],
                    textposition="outside",
                ))
                if _is_new_goals:
                    fig_yr.add_hline(y=_yr_tgt, line_color="rgba(38,166,154,0.7)",
                                     line_dash="dash",
                                     annotation_text=f"Target ≥{_yr_tgt}%",
                                     annotation_position="top right")
                else:
                    fig_yr.add_hrect(y0=GOAL_MR_MIN, y1=GOAL_MR_MAX,
                                     fillcolor="rgba(38,166,154,0.08)", line_width=0,
                                     annotation_text="Target 3–5%", annotation_position="top right")
                fig_yr.update_layout(
                    title="Average Monthly Return by Year",
                    xaxis_title="Year", yaxis_title="Avg Monthly %",
                    template="plotly_dark", height=300,
                    margin=dict(l=10, r=10, t=45, b=10),
                )
                st.plotly_chart(fig_yr, use_container_width=True, key=f"{chart_key_prefix}_yearly")
                st.divider()

            st.dataframe(pd.DataFrame(monthly).rename(columns={
                "month": "Month", "trades": "Trades", "wins": "Wins",
                "losses": "Losses", "net_r": "Net R", "return_pct": "Return %",
            }), use_container_width=True, hide_index=True)

    # ── Trade Log ─────────────────────────────────────────────────────────────
    with ttr:
        if trades:
            df_tr = pd.DataFrame(trades)
            df_tr["Result"] = df_tr["result"].map(
                {"win": "✅ Win", "loss": "❌ Loss", "open": "⏳ Open"})
            col_map = {
                "date": "Date", "time": "Time", "direction": "Dir",
                "swept_level": "Level", "entry": "Entry", "sl": "SL", "tp": "TP",
                "exit_price": "Exit", "Result": "Result",
                "r_multiple": "R", "bars_held": "Bars",
            }
            disp = df_tr[[c for c in col_map if c in df_tr.columns]].rename(columns=col_map)
            st.dataframe(disp, use_container_width=True, hide_index=True)
        else:
            st.info("No signals detected. Try a smaller Swing Strength or a wider date range.")

    # ── Goals ─────────────────────────────────────────────────────────────────
    with tgo:
        gd  = rpt.get("goal_detail", {})
        _IC = {"PASS": "✅", "FAIL": "❌"}
        _GC = {"PASS": "goal-pass", "WATCHLIST": "goal-watch", "FAIL": "goal-fail"}

        # Show goal profile used
        _gp_name = (
            result.get("goal_profile_name")
            or rpt.get("goal_profile_name")
            or ("Legacy Goal Profile" if not _is_new_goals else None)
        )
        if _gp_name:
            st.caption(f"**Goal Profile Used:** {_gp_name}")
        st.write("")

        # Overall status badge
        _gs = rpt.get("goal_status", "INSUFFICIENT DATA")
        _gs_bc = _STATUS_CLASS.get(_gs, "badge-insuf")
        st.markdown(
            f"**Overall Status:** &nbsp;<span class='badge {_gs_bc}'>{_gs}</span>",
            unsafe_allow_html=True,
        )
        st.write("")

        # Per-metric display (handles both old 3-metric and new 4-metric formats)
        if _is_new_goals:
            _metrics = [
                ("Monthly Return",   "monthly_return",   "Avg monthly % return over the backtest period."),
                ("Daily Drawdown",   "daily_drawdown",   "Worst single-day equity loss (sum of trades closing that day)."),
                ("Monthly Drawdown", "monthly_drawdown", "Worst single-month equity loss."),
                ("Profit Factor",    "profit_factor",    "Gross profit ÷ gross loss (in R)."),
            ]
        else:
            _metrics = [
                ("Monthly Return", "monthly_return", "Avg monthly % gain over the backtest period."),
                ("Max Drawdown",   "max_drawdown",   "Largest peak-to-trough equity decline."),
                ("Profit Factor",  "profit_factor",  "Gross profit ÷ gross loss (in R)."),
            ]

        for gl, key, hint in _metrics:
            g   = gd.get(key, {})
            s   = g.get("status", "—")
            val = g.get("value", "N/A")
            tgt = g.get("target", "—")
            ico = _IC.get(s, "⚠️")
            val_str = f"{val}%" if key in ("monthly_return", "daily_drawdown", "monthly_drawdown", "max_drawdown") else str(val)
            st.markdown(
                f'<div class="{_GC.get(s, "")}">'
                f'{ico} &nbsp;<strong>{gl}</strong>'
                f'<br>&nbsp;&nbsp;&nbsp;{val_str} &nbsp;/&nbsp; Target: <code>{tgt}</code>'
                f'<br>&nbsp;&nbsp;&nbsp;<em>{hint}</em></div><br>',
                unsafe_allow_html=True,
            )

        # PASS/WATCHLIST/FAIL legend
        if _is_new_goals:
            st.caption("PASS = all 4 targets met · WATCHLIST = 3/4 (75%+) · FAIL = fewer than 3/4")
        else:
            st.caption("PASS = all targets met · WATCHLIST = partial · FAIL = targets missed")

        st.divider()
        param_rows = [
            ("Symbol",        "XAUUSD"),
            ("TF Mode",       result.get("analysis_mode", "—")),
            ("Analysis Mode", _SUB_MODE_LABELS.get(sub_mode, sub_mode)),
            ("Timeframe(s)",  ", ".join(_sorted_tfs(tfs_used))),
            ("Module",        result.get("module", "—").replace("_", " ").title()),
            ("Risk / Trade",  f"{params.get('risk_pct', risk_pct_val)}%"),
            ("RR Ratio",      f"{params.get('rr', rr)}R"),
            ("Swing N",       params.get("lookback", lookback)),
        ]
        if bt_start:
            param_rows.append(("Analysis Start", str(bt_start)))
        if bt_end:
            param_rows.append(("Analysis End",   str(bt_end)))
        st.dataframe(
            pd.DataFrame(param_rows, columns=["Parameter", "Value"]),
            use_container_width=True, hide_index=True,
        )

    exports = result.get("exports", {})
    if exports:
        with st.expander("📁 Exported files"):
            for k, v in exports.items():
                st.code(str(v), language=None)


# ═════════════════════════════════════════════════════════════════════════════
# TABS
# ═════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "▶ Run Analysis",
    "📂 Dataset Library",
    "🕒 Research History",
    "📤 Export Center",
    "🏥 Platform Health",
    "⚖️ Compare Modules",
    "🎯 Goal Profiles",
])


# ═════════════════════════ TAB 1 — RUN ANALYSIS ═══════════════════════════════
with tab1:

    # ── Step 1 — Data Source ──────────────────────────────────────────────────
    st.subheader("Step 1 — Select Data Source")
    data_src = st.radio(
        "Source",
        ["Upload New Files", "Use Stored Datasets"],
        horizontal=True,
        help=(
            "**Upload New Files**: drag & drop CSV → stored in dataset library.\n\n"
            "**Use Stored Datasets**: pick from previously uploaded datasets."
        ),
    )

    if st.session_state.get("_data_source") != data_src:
        for k in ("_tf_datasets", "_upload_status", "_upload_last_files",
                  "analysis", "analysis_wf", "analysis_sub_mode"):
            st.session_state.pop(k, None)
        st.session_state["_data_source"] = data_src

    # ── UPLOAD NEW FILES ──────────────────────────────────────────────────────
    if data_src == "Upload New Files":
        st.caption(
            "Drag & drop one or more MT5 OHLCV CSV files.  \n"
            "Timeframe auto-detected from filename: `XAUUSD_H1_OHLCV.csv` → H1.  \n"
            "Files are stored in the Dataset Library for future reuse."
        )

        uploaded_files = st.file_uploader(
            "csv", type=["csv"], accept_multiple_files=True,
            label_visibility="collapsed",
        )

        if uploaded_files:
            current_key = frozenset(f.name for f in uploaded_files)
            if st.session_state.get("_upload_last_files") != current_key:
                for k in ("_tf_datasets", "_upload_status", "analysis", "analysis_wf"):
                    st.session_state.pop(k, None)
                st.session_state["_upload_last_files"] = current_key

            if "_tf_datasets" not in st.session_state:
                st.markdown("**Detected timeframes — override if needed:**")
                hdr = st.columns([4, 2, 1])
                hdr[0].markdown("**Filename**")
                hdr[1].markdown("**Timeframe**")
                hdr[2].markdown("**Auto?**")

                for f in uploaded_files:
                    auto_tf = _detect_tf(f.name)
                    row = st.columns([4, 2, 1])
                    row[0].text(f.name)
                    if auto_tf and auto_tf in TF_ALL:
                        row[1].selectbox(
                            f"tf_{f.name}", TF_ALL, index=TF_ALL.index(auto_tf),
                            key=f"tf_{f.name}", label_visibility="collapsed",
                        )
                        row[2].markdown("✅")
                    else:
                        row[1].selectbox(
                            f"tf_{f.name}", ["Select…"] + TF_ALL,
                            key=f"tf_{f.name}", label_visibility="collapsed",
                        )
                        row[2].markdown("⚠️")

                has_unknown = any(
                    st.session_state.get(f"tf_{f.name}", "Select…") == "Select…"
                    for f in uploaded_files
                )
                if has_unknown:
                    st.caption("⚠️  Assign a timeframe to every file before uploading.")

                if st.button("Upload & Store Files", type="primary", disabled=has_unknown):
                    new_datasets: dict[str, str] = {}
                    new_status: list[dict] = []

                    with st.spinner("Uploading and storing files…"):
                        for f in uploaded_files:
                            tf = st.session_state.get(f"tf_{f.name}", "Select…")
                            if tf == "Select…":
                                continue
                            if tf in new_datasets:
                                new_status.append({
                                    "Timeframe": tf, "Filename": f.name,
                                    "Rows": "—", "Start": "—", "End": "—",
                                    "Status": f"⚠️ Skipped (duplicate {tf})",
                                })
                                continue
                            try:
                                info2 = svc.upload_dataset(f.getvalue(), f.name, tf)
                                warns = len(info2.get("validation", {}).get("warnings", []))
                                new_datasets[tf] = info2["dataset_id"]
                                new_status.append({
                                    "Timeframe":  tf,
                                    "Filename":   f.name,
                                    "Rows":       f"{info2['rows']:,}",
                                    "Start":      info2["start"][:10],
                                    "End":        info2["end"][:10],
                                    "Status":     "✅ Stored" if warns == 0 else f"⚠️ {warns} warning(s)",
                                    "Dataset ID": info2["dataset_id"][:8],
                                })
                            except svc.SvcError as _e:
                                if _e.status_code == 409:
                                    _d  = _e.detail
                                    eid = _d.get("existing_dataset_id", "")
                                    etf = _d.get("existing_timeframe", tf)
                                    if eid:
                                        new_datasets[tf] = eid
                                        new_status.append({
                                            "Timeframe": tf, "Filename": f.name,
                                            "Rows": "—", "Start": "—", "End": "—",
                                            "Status":     f"♻️ Reusing existing ({eid[:8]})",
                                            "Dataset ID": eid[:8],
                                        })
                                        st.warning(
                                            f"⚠️ **{f.name}** already in library — "
                                            f"reusing existing {etf} dataset."
                                        )
                                    else:
                                        new_status.append({
                                            "Timeframe": tf, "Filename": f.name,
                                            "Rows": "—", "Start": "—", "End": "—",
                                            "Status": "❌ Duplicate detected", "Dataset ID": "—",
                                        })
                                else:
                                    new_status.append({
                                        "Timeframe": tf, "Filename": f.name,
                                        "Rows": "—", "Start": "—", "End": "—",
                                        "Status": f"❌ {_e.message}", "Dataset ID": "—",
                                    })

                    st.session_state["_tf_datasets"]  = new_datasets
                    st.session_state["_upload_status"] = new_status
                    _clear_data_cache()
                    st.rerun()

        elif not uploaded_files:
            st.info("Drop one or more MT5 OHLCV CSV files above to begin.")

        if "_upload_status" in st.session_state:
            st.dataframe(
                pd.DataFrame(st.session_state["_upload_status"]),
                use_container_width=True, hide_index=True,
            )

    # ── USE STORED DATASETS ───────────────────────────────────────────────────
    else:
        all_datasets = _fetch_datasets()
        if not all_datasets:
            st.warning(
                "No datasets in library yet. "
                "Switch to **Upload New Files** to add your first dataset."
            )
            st.stop()

        st.caption("Pick datasets from the library for your analysis.")
        tf_mode_sel = st.radio(
            "Timeframe Mode",
            ["Single Timeframe", "Multi-Timeframe"],
            horizontal=True,
            key="stored_mode",
        )

        ds_by_id = {d["dataset_id"]: d for d in all_datasets}
        labels   = {d["dataset_id"]: _dataset_label(d) for d in all_datasets}
        ids      = [d["dataset_id"] for d in all_datasets]

        new_datasets: dict[str, str] = {}

        if tf_mode_sel == "Single Timeframe":
            chosen_id = st.selectbox(
                "Select dataset", ids, format_func=lambda x: labels.get(x, x),
            )
            new_datasets = {ds_by_id[chosen_id]["timeframe"]: chosen_id}

        else:
            st.markdown("Assign datasets to roles. **Structure TF** is required.")
            col_tr, col_st, col_en = st.columns(3)
            with col_tr:
                st.markdown("**Trend TF** *(optional)*")
                tr_sel = st.selectbox(
                    "Trend", ["None"] + ids,
                    format_func=lambda x: "None" if x == "None" else labels.get(x, x),
                    key="stored_trend", label_visibility="collapsed",
                )
            with col_st:
                st.markdown("**Structure TF** *(required)*")
                st_sel = st.selectbox(
                    "Structure", ids,
                    format_func=lambda x: labels.get(x, x),
                    key="stored_structure", label_visibility="collapsed",
                )
            with col_en:
                st.markdown("**Entry TF** *(optional)*")
                en_sel = st.selectbox(
                    "Entry", ["None"] + ids,
                    format_func=lambda x: "None" if x == "None" else labels.get(x, x),
                    key="stored_entry", label_visibility="collapsed",
                )
            if st_sel:
                new_datasets[ds_by_id[st_sel]["timeframe"]] = st_sel
            if tr_sel and tr_sel != "None":
                new_datasets[ds_by_id[tr_sel]["timeframe"]] = tr_sel
            if en_sel and en_sel != "None":
                new_datasets[ds_by_id[en_sel]["timeframe"]] = en_sel

        if st.button("Use Selected Datasets", type="secondary"):
            st.session_state["_tf_datasets"] = new_datasets
            for k in ("analysis", "analysis_wf", "analysis_sub_mode"):
                st.session_state.pop(k, None)
            st.rerun()

    # ── Readiness banner ──────────────────────────────────────────────────────
    tf_datasets = st.session_state.get("_tf_datasets", {})
    if tf_datasets:
        label_str = ", ".join(_sorted_tfs(list(tf_datasets.keys())))
        st.success(f"Datasets ready for analysis: **{label_str}**")

    if not tf_datasets:
        st.stop()

    # ── Step 2 — Dataset Info + Date Range ────────────────────────────────────
    st.divider()
    st.subheader("Step 2 — Date Range & Analysis Mode")

    # Fetch fresh metadata for all selected datasets
    all_ds_fresh = _fetch_datasets()
    ds_info_map  = {d["dataset_id"]: d for d in all_ds_fresh}

    info_rows:    list[dict] = []
    avail_starts: list[date] = []
    avail_ends:   list[date] = []

    for tf_key in _sorted_tfs(list(tf_datasets.keys())):
        did = tf_datasets[tf_key]
        d   = ds_info_map.get(did, {})
        s_date = _parse_date(d.get("start_datetime", "")) if d else None
        e_date = _parse_date(d.get("end_datetime", ""))   if d else None
        if s_date:
            avail_starts.append(s_date)
        if e_date:
            avail_ends.append(e_date)
        info_rows.append({
            "TF":             tf_key,
            "Dataset":        d.get("filename", did[:12] + "…") if d else did[:12] + "…",
            "Rows":           f"{d.get('total_rows', 0):,}" if d else "—",
            "Available Start": str(s_date) if s_date else "—",
            "Available End":   str(e_date) if e_date else "—",
            "Dataset ID":     did[:8] + "…",
        })

    if info_rows:
        st.dataframe(pd.DataFrame(info_rows), use_container_width=True, hide_index=True)

    # Compute effective intersection range
    avail_start: date | None = None
    avail_end:   date | None = None
    bt_start_val: date | None = None
    bt_end_val:   date | None = None
    sub_mode_api   = "full_backtest"
    sub_mode_label = "Full Backtest"
    wf_split_date: date | None = None

    if avail_starts and avail_ends:
        avail_start = max(avail_starts)   # latest start = effective start of overlap
        avail_end   = min(avail_ends)     # earliest end  = effective end of overlap

        if avail_start >= avail_end:
            st.error(
                "No overlapping date range across the selected datasets. "
                "Choose datasets that share a common time period."
            )
            st.stop()

        da, db = st.columns(2)
        da.info(f"**Dataset Start:** {avail_start}")
        db.info(f"**Dataset End:** {avail_end}")

        use_full = st.checkbox(
            "Use Full Dataset Range",
            value=True,
            key="use_full_range",
            help="Uncheck to specify a custom backtest window within the dataset range.",
        )

        if use_full:
            bt_start_val = avail_start
            bt_end_val   = avail_end
            span_days    = (bt_end_val - bt_start_val).days
            st.caption(
                f"Backtest range: **{bt_start_val}** → **{bt_end_val}**"
                f"  ({span_days} days · {span_days / 30.44:.1f} months)"
            )
        else:
            dc1, dc2 = st.columns(2)
            with dc1:
                bt_start_val = st.date_input(
                    "Backtest Start Date",
                    value=avail_start,
                    min_value=avail_start,
                    max_value=avail_end,
                    key="bt_start_input",
                )
            with dc2:
                bt_end_val = st.date_input(
                    "Backtest End Date",
                    value=avail_end,
                    min_value=avail_start,
                    max_value=avail_end,
                    key="bt_end_input",
                )

            if bt_start_val >= bt_end_val:
                st.error("❌ Backtest Start must be before Backtest End.")
                st.stop()
            if bt_start_val < avail_start or bt_end_val > avail_end:
                st.error(
                    f"❌ Selected range [{bt_start_val} → {bt_end_val}] falls outside "
                    f"dataset range [{avail_start} → {avail_end}]."
                )
                st.stop()

            span_days = (bt_end_val - bt_start_val).days
            st.caption(
                f"Backtest span: **{span_days} days** ({span_days / 30.44:.1f} months)"
            )

        # Analysis sub-mode
        sub_mode_label = st.radio(
            "Analysis Mode",
            ["Full Backtest", "Yearly Analysis", "Monthly Analysis", "Walk Forward Analysis"],
            horizontal=True,
            key="sub_mode_radio",
            help=(
                "**Full Backtest**: single run, full metrics.  \n"
                "**Yearly Analysis**: one run — year-by-year breakdown in Monthly tab.  \n"
                "**Monthly Analysis**: one run — monthly detail highlighted.  \n"
                "**Walk Forward**: in-sample training + out-of-sample validation."
            ),
        )
        sub_mode_api = _SUB_MODE_API[sub_mode_label]

        # Walk Forward split controls
        wf_is_pct = 70
        if sub_mode_label == "Walk Forward Analysis":
            wf_is_pct = st.slider(
                "In-Sample period",
                min_value=50, max_value=90, value=70, step=5,
                format="%d%%",
                key="wf_is_pct",
                help="Percentage of the date range used for in-sample (training) data.",
            )
            total_days    = max((bt_end_val - bt_start_val).days, 1)
            is_days       = int(total_days * wf_is_pct / 100)
            wf_split_date = bt_start_val + timedelta(days=is_days)
            oos_start     = wf_split_date + timedelta(days=1)

            wi, wo = st.columns(2)
            wi.info(f"**In-Sample:** {bt_start_val} → {wf_split_date} ({is_days} days)")
            wo.info(f"**Out-of-Sample:** {oos_start} → {bt_end_val} ({total_days - is_days} days)")

    else:
        st.warning("Could not load dataset metadata. Date range selection unavailable.")

    # ── Step 3 — Configure & Run ──────────────────────────────────────────────
    st.divider()
    st.subheader("Step 3 — Configure & Run")

    if data_src == "Upload New Files":
        analysis_mode = st.radio(
            "Timeframe Mode",
            ["Single Timeframe", "Multi-Timeframe"],
            horizontal=True,
            key="upload_mode",
        )
    else:
        analysis_mode = tf_mode_sel

    trend_tf = structure_tf = entry_tf = None
    display_tf     = _sorted_tfs(list(tf_datasets.keys()))[0]
    req_dataset_id = None
    ok_tfs_sorted  = _sorted_tfs(list(tf_datasets.keys()))

    if analysis_mode == "Single Timeframe":
        if len(ok_tfs_sorted) == 1:
            sel_tf = ok_tfs_sorted[0]
        else:
            sel_tf = st.selectbox("Analysis Timeframe", ok_tfs_sorted, key="single_tf_sel")
        req_dataset_id = tf_datasets[sel_tf]
        display_tf     = sel_tf

    elif analysis_mode == "Multi-Timeframe" and data_src == "Upload New Files":
        st.markdown("Assign roles to your uploaded timeframes. **Structure TF** is required.")
        col_t2, col_s2, col_e2 = st.columns(3)
        with col_t2:
            st.markdown("**Trend TF** *(optional)*")
            tr2 = st.selectbox("Trend TF", ["None"] + ok_tfs_sorted,
                               key="ul_trend_tf", label_visibility="collapsed")
            trend_tf = None if tr2 == "None" else tr2
        with col_s2:
            st.markdown("**Structure TF** *(required)*")
            structure_tf = st.selectbox("Structure TF", ok_tfs_sorted,
                                        key="ul_structure_tf", label_visibility="collapsed")
            display_tf   = structure_tf
        with col_e2:
            st.markdown("**Entry TF** *(optional)*")
            en2 = st.selectbox("Entry TF", ["None"] + ok_tfs_sorted,
                               key="ul_entry_tf", label_visibility="collapsed")
            entry_tf = None if en2 == "None" else en2

    else:  # Multi-TF stored — roles set in Step 1
        if data_src == "Use Stored Datasets":
            ds_by_id2  = {d["dataset_id"]: d for d in all_ds_fresh}
            st_sel_id  = st.session_state.get("stored_structure")
            tr_sel_id  = st.session_state.get("stored_trend")
            en_sel_id  = st.session_state.get("stored_entry")
            if st_sel_id and st_sel_id in ds_by_id2:
                structure_tf = ds_by_id2[st_sel_id]["timeframe"]
                display_tf   = structure_tf
            if tr_sel_id and tr_sel_id != "None" and tr_sel_id in ds_by_id2:
                trend_tf = ds_by_id2[tr_sel_id]["timeframe"]
            if en_sel_id and en_sel_id != "None" and en_sel_id in ds_by_id2:
                entry_tf = ds_by_id2[en_sel_id]["timeframe"]

    research_name = st.text_input(
        "Research Name (optional)",
        placeholder="e.g. XAUUSD H1 Sweep — Jan 2024",
        key="research_name_input",
    )

    col_btn, col_desc = st.columns([1, 5])
    with col_btn:
        run_btn = st.button("▶  Run Analysis", type="primary", use_container_width=True)
    with col_desc:
        mode_label = "Single TF" if analysis_mode == "Single Timeframe" else "Multi-TF"
        _mod_display = _module_label(module)
        st.caption(
            f"**{_mod_display}** | **{mode_label}** | **{sub_mode_label}** | "
            f"Risk: **{risk_pct}%** | RR: **{rr}R** | Swing N: **{lookback}**"
        )
        st.caption(f"🔬 Current Selected Module: `{module}`")
        if module not in _MODULE_LABELS:
            print(f"[warn] Unknown module in session: {module!r}", flush=True)
            st.warning(f"⚠️ Unknown module: {module!r} — not in registered module list.")

    if run_btn:
        errors: list[str] = []
        if analysis_mode == "Multi-Timeframe":
            if not structure_tf:
                errors.append("Structure Timeframe is required for Multi-TF analysis.")
            elif structure_tf not in tf_datasets:
                errors.append(f"Missing {structure_tf} data.")
            if trend_tf and trend_tf not in tf_datasets:
                errors.append(f"Missing {trend_tf} data.")
            if entry_tf and entry_tf not in tf_datasets:
                errors.append(f"Missing {entry_tf} data.")
        if errors:
            for e in errors:
                st.error(f"❌ {e}")
            st.stop()

        bt_start_str = str(bt_start_val) if bt_start_val else None
        bt_end_str   = str(bt_end_val)   if bt_end_val   else None

        def _make_payload(name_suffix: str = "", start: str | None = None, end: str | None = None) -> dict:
            p: dict = {
                "analysis_mode":     "single" if analysis_mode == "Single Timeframe" else "multi",
                "module":            module,
                "timeframe":         display_tf,
                "risk_pct":          risk_pct,
                "rr":                rr,
                "lookback":          lookback,
                "breakout_buffer":   breakout_buffer,
                "retest_tolerance":  retest_tolerance,
                "research_name":     (research_name + name_suffix) if research_name else (
                    name_suffix.strip() or None
                ),
                "analysis_sub_mode": sub_mode_api,
                "backtest_start":    start,
                "backtest_end":      end,
                "goal_profile_id":   goal_profile_id,
            }
            if analysis_mode == "Single Timeframe":
                p["dataset_id"] = req_dataset_id
            else:
                p["dataset_ids"]  = tf_datasets
                p["trend_tf"]     = trend_tf
                p["structure_tf"] = structure_tf
                p["entry_tf"]     = entry_tf
            return p

        if sub_mode_label == "Walk Forward Analysis" and wf_split_date:
            # ── Two runs: IS then OOS ───────────────────────────────────────
            oos_start_date = wf_split_date + timedelta(days=1)
            is_payload  = _make_payload(" [IS]",  bt_start_str, str(wf_split_date))
            oos_payload = _make_payload(" [OOS]", str(oos_start_date), bt_end_str)

            with st.spinner("Walk Forward — running In-Sample period…"):
                try:
                    is_result = svc.run_analysis(is_payload)
                except svc.SvcError as _e:
                    st.error(f"In-Sample failed: {_e.message}")
                    st.stop()

            with st.spinner("Walk Forward — running Out-of-Sample period…"):
                try:
                    oos_result = svc.run_analysis(oos_payload)
                except svc.SvcError as _e:
                    st.error(f"Out-of-Sample failed: {_e.message}")
                    st.stop()

            st.session_state["analysis_wf"] = {
                "is":  is_result,
                "oos": oos_result,
                "split_date": str(wf_split_date),
                "is_pct": wf_is_pct,
            }
            st.session_state.pop("analysis", None)
            _clear_data_cache()
            st.success(
                f"Walk Forward complete — "
                f"IS: **{is_result.get('research_id','')[:8]}** | "
                f"OOS: **{oos_result.get('research_id','')[:8]}**"
            )

        else:
            # ── Single run ──────────────────────────────────────────────────
            payload = _make_payload("", bt_start_str, bt_end_str)
            _t_run  = time.perf_counter()
            with st.spinner("Running backtest — detecting swings, executing trades…"):
                try:
                    _result = svc.run_analysis(payload)
                except svc.SvcError as _e:
                    st.error(f"Analysis failed: {_e.message}")
                    st.stop()
            print(f"[perf] run_analysis: {(time.perf_counter()-_t_run)*1000:.0f}ms", flush=True)

            st.session_state["analysis"] = _result
            st.session_state.pop("analysis_wf", None)
            _clear_data_cache()
            st.success(
                f"Analysis complete — "
                f"Research ID: **{_result.get('research_id','')[:8]}**"
            )

    # ── Step 4 — Results ──────────────────────────────────────────────────────
    if "analysis_wf" in st.session_state:
        st.divider()
        st.subheader("Step 4 — Walk Forward Results")

        wf    = st.session_state["analysis_wf"]
        is_r  = wf["is"]
        oos_r = wf["oos"]

        is_pf  = is_r.get("report",  {}).get("profit_factor", 0.0)
        oos_pf = oos_r.get("report", {}).get("profit_factor", 0.0)
        wfe    = round(oos_pf / is_pf, 3) if is_pf > 0 else 0.0
        wfe_label = (
            "✅ Robust (≥ 0.7)" if wfe >= 0.7
            else "⚠️ Degraded (0.4–0.69)"  if wfe >= 0.4
            else "❌ Overfit (< 0.4)"
        )

        st.markdown(
            f"**Walk Forward Efficiency:** `{wfe:.2f}x`  "
            f"*(OOS PF {oos_pf:.2f} / IS PF {is_pf:.2f})*  —  {wfe_label}"
        )
        st.write("")

        def _rpt(res: dict) -> dict:
            return res.get("report", {})

        cmp = [
            {"Metric": "Period",       "In-Sample": f"{wf.get('is_pct', 70)}%", "Out-of-Sample": f"{100 - wf.get('is_pct', 70)}%"},
            {"Metric": "Range",        "In-Sample": f"{is_r.get('backtest_start','—')} → {is_r.get('backtest_end','—')}", "Out-of-Sample": f"{oos_r.get('backtest_start','—')} → {oos_r.get('backtest_end','—')}"},
            {"Metric": "Trades",       "In-Sample": _rpt(is_r).get("total_trades", 0),      "Out-of-Sample": _rpt(oos_r).get("total_trades", 0)},
            {"Metric": "Win%",         "In-Sample": f"{_rpt(is_r).get('win_rate', 0):.1f}", "Out-of-Sample": f"{_rpt(oos_r).get('win_rate', 0):.1f}"},
            {"Metric": "Profit Factor","In-Sample": f"{_rpt(is_r).get('profit_factor', 0):.2f}", "Out-of-Sample": f"{_rpt(oos_r).get('profit_factor', 0):.2f}"},
            {"Metric": "Net R",        "In-Sample": f"{_rpt(is_r).get('net_r', 0):+.2f}",   "Out-of-Sample": f"{_rpt(oos_r).get('net_r', 0):+.2f}"},
            {"Metric": "Avg Monthly%", "In-Sample": f"{_rpt(is_r).get('monthly_return', 0):.2f}", "Out-of-Sample": f"{_rpt(oos_r).get('monthly_return', 0):.2f}"},
            {"Metric": "Max DD%",      "In-Sample": f"{_rpt(is_r).get('max_drawdown', 0):.2f}", "Out-of-Sample": f"{_rpt(oos_r).get('max_drawdown', 0):.2f}"},
            {"Metric": "Goal Status",  "In-Sample": _rpt(is_r).get("goal_status", "—"),     "Out-of-Sample": _rpt(oos_r).get("goal_status", "—")},
        ]
        st.dataframe(pd.DataFrame(cmp), use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("### In-Sample Results")
        _render_results(is_r, risk_pct, chart_key_prefix="wf_is")
        st.divider()
        st.markdown("### Out-of-Sample Results")
        _render_results(oos_r, risk_pct, chart_key_prefix="wf_oos")

    elif "analysis" in st.session_state:
        st.divider()
        st.subheader("Step 4 — Results")
        _render_results(st.session_state["analysis"], risk_pct, chart_key_prefix="main")


# ═════════════════════════ TAB 2 — DATASET LIBRARY ════════════════════════════
with tab2:
    st.subheader("📂 OHLCV Dataset Library")
    st.caption(
        "All stored datasets. "
        "Datasets survive server restarts and can be reused across analysis sessions."
    )

    datasets = _fetch_datasets()

    if not datasets:
        st.info(
            "No datasets stored yet. "
            "Upload OHLCV CSV files in **Run Analysis → Upload New Files**."
        )
    else:
        tbl_rows = []
        for d in datasets:
            tbl_rows.append({
                "Timeframe": d["timeframe"],
                "Filename":  d["filename"],
                "Rows":      f"{d['total_rows']:,}",
                "Start":     d["start_datetime"][:10],
                "End":       d["end_datetime"][:10],
                "Status":    d["status"],
                "Dataset ID": d["dataset_id"][:12] + "…",
                "_id":       d["dataset_id"],
            })
        tbl_df = pd.DataFrame(tbl_rows)
        st.dataframe(tbl_df.drop(columns=["_id"]), use_container_width=True, hide_index=True)
        st.caption(f"{len(datasets)} dataset(s) in library.")

        st.divider()
        st.markdown("**Actions — select a dataset:**")
        sel_idx = st.selectbox(
            "Dataset",
            range(len(datasets)),
            format_func=lambda i: _dataset_label(datasets[i]),
            key="lib_sel_idx",
        )
        sel_d  = datasets[sel_idx]
        sel_id = sel_d["dataset_id"]

        col_a, col_b, col_c = st.columns([1, 1, 3])
        with col_a:
            try:
                _exp_bytes = svc.export_dataset_csv(sel_id)
                st.download_button(
                    "⬇️  Export CSV", data=_exp_bytes,
                    file_name=f"{sel_d['symbol']}_{sel_d['timeframe']}_{sel_id[:8]}.csv",
                    mime="text/csv", key=f"dl_{sel_id}",
                )
            except Exception:
                st.button("⬇️  Export CSV", disabled=True)

        with col_b:
            if st.button("🗑️  Delete", key=f"del_{sel_id}", type="secondary"):
                st.session_state["_confirm_delete_dataset"] = sel_id

        with col_c:
            st.markdown(
                f"**ID:** `{sel_id}`  |  **TF:** {sel_d['timeframe']}  |  "
                f"**{sel_d['total_rows']:,} rows**  |  "
                f"{sel_d['start_datetime'][:10]} → {sel_d['end_datetime'][:10]}"
            )

        if st.session_state.get("_confirm_delete_dataset") == sel_id:
            st.warning(
                f"Delete **{sel_d['filename']}** ({sel_d['timeframe']}) "
                f"and all its candles? This cannot be undone."
            )
            cc1, cc2 = st.columns(2)
            if cc1.button("✅ Yes, delete", type="primary", key="confirm_del"):
                try:
                    svc.delete_dataset(sel_id)
                    st.success("Dataset deleted.")
                    st.session_state.pop("_confirm_delete_dataset", None)
                    _clear_data_cache()
                    st.rerun()
                except svc.SvcError as _e:
                    st.error(f"Delete failed: {_e.message}")
            if cc2.button("✗ Cancel", key="cancel_del"):
                st.session_state.pop("_confirm_delete_dataset", None)


# ═════════════════════════ TAB 3 — RESEARCH HISTORY ══════════════════════════
with tab3:
    st.subheader("🕒 Research History")
    st.caption("All completed analysis runs, stored persistently in SQLite.")

    _hist_col1, _hist_col2 = st.columns([3, 1])
    with _hist_col2:
        hist_limit = st.selectbox(
            "Show last N runs", [25, 50, 100, 200], index=1, key="hist_limit"
        )
    with _hist_col1:
        if st.button("🔄 Refresh History", key="hist_refresh"):
            _clear_data_cache()
            st.rerun()

    runs = _fetch_research_runs(limit=hist_limit)

    if not runs:
        st.info("No research runs yet. Run an analysis in **Run Analysis** to create records.")
    else:
        summary_rows = []
        for r in runs:
            tfs = json.loads(r.get("timeframes_used", "[]"))
            summary_rows.append({
                "Name":            r.get("research_name", "—"),
                "Date/Time (MYT)": _to_myt(r.get("created_datetime")),
                "Module":          _module_label(r.get("selected_module")),
                "Mode":            r["timeframe_mode"],
                "TF(s)":           ", ".join(tfs),
                "Analysis":        _SUB_MODE_LABELS.get(r.get("analysis_sub_mode", ""), "—"),
                "Range":           (
                    f"{r['backtest_start'][:10] if r.get('backtest_start') else '—'}"
                    f" → "
                    f"{r['backtest_end'][:10] if r.get('backtest_end') else '—'}"
                ),
                "Trades":          r["total_trades"],
                "Win%":            f"{r['win_rate']:.1f}",
                "PF":              f"{r['profit_factor']:.2f}",
                "Net R":           f"{r['net_r']:+.2f}",
                "Monthly%":        f"{r['monthly_return']:.2f}",
                "DD%":             f"{r['max_drawdown']:.2f}",
                "Status":          r["goal_status"],
                "_id":             r["research_id"],
            })

        sum_df = pd.DataFrame(summary_rows)
        st.dataframe(sum_df.drop(columns=["_id"]), use_container_width=True, hide_index=True)
        st.caption(f"{len(runs)} research run(s) on record.")

        st.divider()
        st.markdown("**Actions — select a research run:**")
        sel_run_idx = st.selectbox(
            "Research Run",
            range(len(runs)),
            format_func=lambda i: (
                f"{runs[i].get('research_name','—')} "
                f"[{_to_myt(runs[i].get('created_datetime'))}] "
                f"{_module_label(runs[i].get('selected_module'))} "
                f"— {runs[i]['total_trades']} trades, {runs[i]['goal_status']}"
            ),
            key="hist_sel_idx",
        )
        sel_run = runs[sel_run_idx]
        sel_rid = sel_run["research_id"]

        col_tl, col_mo, col_su, col_rj, col_del = st.columns(5)
        for fmt, label, mime, col in [
            ("trade_log", "Trade Log CSV", "text/csv",         col_tl),
            ("monthly",   "Monthly CSV",   "text/csv",         col_mo),
            ("summary",   "Summary CSV",   "text/csv",         col_su),
            ("report",    "Report JSON",   "application/json", col_rj),
        ]:
            with col:
                try:
                    _eb = svc.export_research(sel_rid, fmt)
                    ext = "json" if fmt == "report" else "csv"
                    col.download_button(
                        f"⬇️ {label}", data=_eb,
                        file_name=f"{fmt}_{sel_rid[:8]}.{ext}",
                        mime=mime, key=f"dl_{fmt}_{sel_rid}",
                    )
                except Exception:
                    col.button(f"⬇️ {label}", disabled=True, key=f"btn_{fmt}_{sel_rid}")

        with col_del:
            if st.button("🗑️ Delete", key=f"del_run_{sel_rid}", type="secondary"):
                st.session_state["_confirm_delete_run"] = sel_rid

        if st.session_state.get("_confirm_delete_run") == sel_rid:
            st.warning("Delete this research run and all associated trade records?")
            dc1, dc2 = st.columns(2)
            if dc1.button("✅ Yes, delete", type="primary", key="confirm_del_run"):
                if svc.delete_research_run(sel_rid):
                    st.success("Research run deleted.")
                    st.session_state.pop("_confirm_delete_run", None)
                    _clear_data_cache()
                    st.rerun()
                else:
                    st.error("Delete failed.")
            if dc2.button("✗ Cancel", key="cancel_del_run"):
                st.session_state.pop("_confirm_delete_run", None)

        st.divider()
        view_col, rerun_col = st.columns(2)

        with view_col:
            with st.expander(f"🔍 View details — {sel_run.get('research_name','—')[:40]}"):
                try:
                    run_detail = svc.get_research_run(sel_rid)
                    if run_detail:
                        full_rpt   = json.loads(run_detail.get("full_report") or "{}")
                        if full_rpt:
                            dm1, dm2 = st.columns(2)
                            dm1.markdown(
                                f"**Module Used:** {_module_label(run_detail.get('selected_module'))}"
                            )
                            dm2.markdown(
                                f"**Created (MYT):** {_to_myt(run_detail.get('created_datetime'))}"
                            )
                            st.write("")
                            fake_result = {
                                "report":           full_rpt,
                                "trades":           [],
                                "analysis_mode":    run_detail.get("timeframe_mode", "single"),
                                "timeframe":        run_detail.get("timeframes_used", "[]"),
                                "timeframes_used":  json.loads(run_detail.get("timeframes_used", "[]")),
                                "structure_tf":     None,
                                "trend_tf":         None,
                                "entry_tf":         None,
                                "module":           run_detail["selected_module"],
                                "backtest_start":   run_detail.get("backtest_start"),
                                "backtest_end":     run_detail.get("backtest_end"),
                                "analysis_sub_mode": run_detail.get("analysis_sub_mode", "full_backtest"),
                                "parameters": {
                                    "risk_pct": run_detail["risk_percent"],
                                    "rr":       run_detail["reward_risk_ratio"],
                                    "lookback": run_detail["lookback"],
                                },
                                "exports": {},
                            }
                            _render_results(fake_result, run_detail["risk_percent"], chart_key_prefix=f"hist_{sel_rid[:8]}")
                        else:
                            st.info("Full report not available for this run.")
                except Exception as exc:
                    st.error(f"Could not load run detail: {exc}")

        with rerun_col:
            with st.expander("🔄 Re-run with same parameters"):
                try:
                    rd = svc.get_research_run(sel_rid)
                    if rd:
                        did_map = json.loads(rd.get("dataset_ids_used", "{}"))
                        st.markdown(
                            f"**Module:** {rd['selected_module'].replace('_',' ').title()}  \n"
                            f"**Mode:** {rd['timeframe_mode']}  \n"
                            f"**Risk:** {rd['risk_percent']}%  |  "
                            f"**RR:** {rd['reward_risk_ratio']}R  |  "
                            f"**Swing N:** {rd['lookback']}"
                        )
                        if rd.get("backtest_start") or rd.get("backtest_end"):
                            st.caption(
                                f"Original range: "
                                f"{rd.get('backtest_start','—')} → {rd.get('backtest_end','—')} "
                                f"| Mode: {_SUB_MODE_LABELS.get(rd.get('analysis_sub_mode',''), '—')}"
                            )
                        if not did_map:
                            st.warning("No stored dataset IDs linked to this run.")
                        else:
                            st.json({"Datasets": did_map})
                            new_name = st.text_input(
                                "Research name for re-run",
                                value=f"Re-run of {rd.get('research_name','')[:30]}",
                                key=f"rerun_name_{sel_rid}",
                            )
                            if st.button("▶ Re-run now", type="primary",
                                         key=f"btn_rerun_{sel_rid}"):
                                tfs = json.loads(rd.get("timeframes_used", "[]"))
                                rr_payload: dict = {
                                    "analysis_mode":     rd["timeframe_mode"],
                                    "module":            rd["selected_module"],
                                    "timeframe":         tfs[0] if tfs else "H1",
                                    "risk_pct":          rd["risk_percent"],
                                    "rr":                rd["reward_risk_ratio"],
                                    "lookback":          rd["lookback"],
                                    "research_name":     new_name,
                                    "backtest_start":    rd.get("backtest_start"),
                                    "backtest_end":      rd.get("backtest_end"),
                                    "analysis_sub_mode": rd.get("analysis_sub_mode", "full_backtest"),
                                }
                                if rd["timeframe_mode"] == "multi":
                                    rr_payload["dataset_ids"] = did_map
                                else:
                                    first_id = next(iter(did_map.values()), None) if did_map else None
                                    if not first_id:
                                        st.error("No dataset linked. Cannot re-run.")
                                        st.stop()
                                    rr_payload["dataset_id"] = first_id

                                with st.spinner("Re-running analysis…"):
                                    try:
                                        rerun_result = svc.run_analysis(rr_payload)
                                        st.success(
                                            f"Re-run complete — "
                                            f"ID: {rerun_result.get('research_id','')[:8]}"
                                        )
                                        _render_results(rerun_result, rd["risk_percent"], chart_key_prefix=f"rerun_{sel_rid[:8]}")
                                    except svc.SvcError as _e:
                                        st.error(f"Re-run failed: {_e.message}")
                except Exception as exc:
                    st.error(f"Could not load run: {exc}")


# ═════════════════════════ TAB 4 — EXPORT CENTER ══════════════════════════════
with tab4:
    st.subheader("📤 Export Center")
    st.caption("Download exports for datasets and research runs.")

    st.markdown("### OHLCV Dataset Exports")
    exp_datasets = _fetch_datasets()
    if not exp_datasets:
        st.info("No datasets in library.")
    else:
        exp_idx = st.selectbox(
            "Select dataset to export",
            range(len(exp_datasets)),
            format_func=lambda i: _dataset_label(exp_datasets[i]),
            key="exp_ds_idx",
        )
        exp_d   = exp_datasets[exp_idx]
        exp_did = exp_d["dataset_id"]
        try:
            _exp_b = svc.export_dataset_csv(exp_did)
            fname  = f"{exp_d['symbol']}_{exp_d['timeframe']}_{exp_did[:8]}.csv"
            st.download_button(
                f"⬇️  Download {exp_d['symbol']} {exp_d['timeframe']} CSV "
                f"({exp_d['total_rows']:,} rows)",
                data=_exp_b, file_name=fname, mime="text/csv", type="primary",
            )
        except svc.SvcError as _e:
            st.error(f"Export failed: {_e.message}")

    st.divider()

    st.markdown("### Research Run Exports")
    exp_runs = _fetch_research_runs(limit=100)
    if not exp_runs:
        st.info("No research runs recorded.")
    else:
        exp_run_idx = st.selectbox(
            "Select research run",
            range(len(exp_runs)),
            format_func=lambda i: (
                f"{exp_runs[i].get('research_name','—')} "
                f"[{_to_myt(exp_runs[i].get('created_datetime'))}] "
                f"{_module_label(exp_runs[i].get('selected_module'))} "
                f"— {exp_runs[i]['total_trades']} trades"
            ),
            key="exp_run_idx",
        )
        exp_run = exp_runs[exp_run_idx]
        exp_rid = exp_run["research_id"]
        st.markdown(
            f"**{exp_run.get('research_name','—')}** · "
            f"{_to_myt(exp_run.get('created_datetime'))} · "
            f"**{_module_label(exp_run.get('selected_module'))}** · "
            f"{exp_run['total_trades']} trades · **{exp_run['goal_status']}**"
        )
        if exp_run.get("backtest_start") or exp_run.get("backtest_end"):
            st.caption(
                f"Range: {exp_run.get('backtest_start','—')} → {exp_run.get('backtest_end','—')}"
                f"  |  Mode: {_SUB_MODE_LABELS.get(exp_run.get('analysis_sub_mode',''), '—')}"
            )

        ec1, ec2, ec3, ec4 = st.columns(4)
        for fmt, label, mime, ext, col in [
            ("trade_log", "Trade Log",      "text/csv",         "csv",  ec1),
            ("monthly",   "Monthly Report", "text/csv",         "csv",  ec2),
            ("summary",   "Summary",        "text/csv",         "csv",  ec3),
            ("report",    "Full Report",    "application/json", "json", ec4),
        ]:
            with col:
                try:
                    _eb2 = svc.export_research(exp_rid, fmt)
                    col.download_button(
                        f"⬇️ {label}", data=_eb2,
                        file_name=f"{fmt}_{exp_rid[:8]}.{ext}",
                        mime=mime, key=f"exp_{fmt}_{exp_rid}",
                    )
                except Exception:
                    col.button(f"⬇️ {label}", disabled=True, key=f"exp_btn_{fmt}_{exp_rid}")

    st.divider()
    st.markdown("### Cumulative Log Files")
    st.caption(
        "These files accumulate across all analysis runs (appended each time).  \n"
        "Location: `data/exports/`"
    )
    st.code(
        "data/exports/trade_log.csv        — all trades (every run)\n"
        "data/exports/research_summary.csv — all run summaries",
        language=None,
    )
    st.markdown(
        "Individual per-run files are also saved after each analysis:  \n"
        "`trade_log_{id}.csv`, `monthly_{id}.csv`, `summary_{id}.csv`, `report_{id}.json`"
    )


# ═════════════════════════ TAB 5 — PLATFORM HEALTH ════════════════════════════
with tab5:
    st.subheader("🏥 Platform Health")
    st.caption("Database integrity, storage stats, and development tools.")

    if st.button("🔄 Refresh", key="health_refresh"):
        st.rerun()

    # ── DB Health ─────────────────────────────────────────────────────────────
    h = svc.db_health()
    if not h and svc.RUN_MODE == "api":
        st.error("Cannot reach backend — is uvicorn running on port 8000?")

    st.markdown("### 🗄️ Database")
    db_ok = h.get("connection") == "ok"

    hc1, hc2, hc3, hc4 = st.columns(4)
    hc1.metric("Connection",    "✅ OK"  if db_ok  else "❌ Error")
    hc2.metric("Foreign Keys",  "✅ ON"  if h.get("foreign_keys_on") else "❌ OFF")
    hc3.metric("File Exists",   "✅ Yes" if h.get("db_file_exists")  else "❌ No")
    hc4.metric("File Size",     f"{h.get('db_file_size_kb', 0)} KB")

    if h.get("db_path"):
        st.code(h["db_path"], language=None)

    if not db_ok and h.get("connection"):
        st.error(f"DB error: {h['connection']}")

    # ── Table Row Counts ──────────────────────────────────────────────────────
    st.markdown("### 📊 Table Counts")
    tables = h.get("tables", {})
    if tables:
        tc1, tc2, tc3, tc4, tc5 = st.columns(5)
        for col, tbl, label in [
            (tc1, "datasets",       "Datasets"),
            (tc2, "ohlcv_candles",  "Candles"),
            (tc3, "research_runs",  "Research Runs"),
            (tc4, "trade_logs",     "Trade Logs"),
            (tc5, "monthly_reports","Monthly Reports"),
        ]:
            v = tables.get(tbl)
            col.metric(label, f"{v:,}" if v is not None else "⚠️ Missing")
    else:
        st.info("No table data available.")

    # ── Latest Research Run ───────────────────────────────────────────────────
    st.markdown("### 🕒 Latest Research Run")
    latest = h.get("latest_research_run")
    if latest:
        lc1, lc2, lc3, lc4 = st.columns(4)
        lc1.markdown(f"**ID:** `{latest.get('research_id','—')[:8]}…`")
        lc2.markdown(f"**Name:** {latest.get('research_name','—')}")
        lc3.markdown(f"**Created (MYT):** {_to_myt(latest.get('created_datetime'))}")
        lc4.markdown(f"**Status:** {latest.get('goal_status','—')}")
    else:
        st.info("No research runs recorded yet.")

    # ── Default Goal Profile ──────────────────────────────────────────────────
    st.markdown("### 🎯 Current Default Goal Profile")
    dgp = h.get("default_goal_profile")
    if dgp:
        gc1, gc2, gc3, gc4, gc5 = st.columns(5)
        gc1.metric("Profile", dgp.get("profile_name", "—"))
        gc2.metric("Monthly Return", f"≥ {dgp.get('monthly_return_target', '—')}%")
        gc3.metric("Daily DD",       f"≤ {dgp.get('daily_drawdown_target', '—')}%")
        gc4.metric("Monthly DD",     f"≤ {dgp.get('monthly_drawdown_target', '—')}%")
        gc5.metric("Profit Factor",  f"≥ {dgp.get('profit_factor_target', '—')}")
    else:
        st.info("No default goal profile found. Go to **🎯 Goal Profiles** tab to set one.")

    # ── Export Folder ─────────────────────────────────────────────────────────
    st.markdown("### 📁 Export Folder")
    export_dir = Path("data/exports")
    if export_dir.exists():
        export_files = sorted(export_dir.iterdir())
        total_kb     = sum(f.stat().st_size for f in export_files if f.is_file()) / 1024
        ec1, ec2, ec3 = st.columns(3)
        ec1.metric("Status",     "✅ Exists")
        ec2.metric("Files",      len([f for f in export_files if f.is_file()]))
        ec3.metric("Total Size", f"{total_kb:.1f} KB")

        if export_files:
            with st.expander("📂 Show export files"):
                for f in export_files:
                    if f.is_file():
                        sz = f.stat().st_size
                        st.markdown(f"`{f.name}` — {sz:,} bytes")
    else:
        st.warning("Export folder `data/exports/` does not exist yet. Run an analysis to create it.")

    # ── Validation Checklist ──────────────────────────────────────────────────
    st.markdown("### ✅ Validation Checklist")
    checks = [
        ("Backend API reachable",        db_ok or h.get("connection") != {}),
        ("Database file exists",         h.get("db_file_exists", False)),
        ("Foreign keys ON",              h.get("foreign_keys_on", False)),
        ("datasets table present",       tables.get("datasets") is not None),
        ("ohlcv_candles table present",  tables.get("ohlcv_candles") is not None),
        ("research_runs table present",  tables.get("research_runs") is not None),
        ("trade_logs table present",     tables.get("trade_logs") is not None),
        ("monthly_reports table present",tables.get("monthly_reports") is not None),
        ("Default goal profile set",     h.get("default_goal_profile") is not None),
        ("At least 1 dataset stored",    (tables.get("datasets") or 0) >= 1),
        ("At least 1 research run saved",(tables.get("research_runs") or 0) >= 1),
        ("Export folder exists",         export_dir.exists()),
    ]
    chk_df = pd.DataFrame([
        {"Check": c, "Result": "✅ Pass" if ok else "❌ Fail"}
        for c, ok in checks
    ])
    st.dataframe(chk_df, use_container_width=True, hide_index=True)

    # ── Reset Database (Dev Only) ─────────────────────────────────────────────
    st.divider()
    st.markdown("### ⚠️ Reset Database — Development Only")
    st.warning(
        "**Danger zone.** This permanently deletes ALL datasets, candles, "
        "research runs, trade logs, and monthly reports. "
        "The schema is recreated from scratch.  \n"
        "Only use this during development to start fresh."
    )

    confirmed_cb = st.checkbox(
        "I understand this will permanently delete ALL stored data.",
        key="reset_confirm_cb",
    )
    reset_phrase = st.text_input(
        "Type **RESET** to enable the button:",
        key="reset_phrase_input",
        disabled=not confirmed_cb,
    )
    reset_ok = confirmed_cb and reset_phrase.strip() == "RESET"

    if st.button(
        "🗑️ Reset Database",
        type="primary",
        disabled=not reset_ok,
        key="reset_db_btn",
    ):
        try:
            svc.reset_db("RESET")
            st.success("✅ Database reset complete. All tables recreated.")
            _clear_data_cache()
            for k in list(st.session_state.keys()):
                st.session_state.pop(k, None)
            st.rerun()
        except svc.SvcError as _e:
            st.error(f"Reset failed: {_e.message}")


# ═════════════════════════ TAB 6 — COMPARE MODULES ════════════════════════════
with tab6:
    st.subheader("⚖️ Module Comparison Dashboard")
    st.caption(
        "Run Liquidity Sweep and Break & Retest on the same dataset and compare results side by side. "
        "Both runs are saved to Research History."
    )

    # ── Dataset selection ─────────────────────────────────────────────────────
    st.markdown("### 1. Select Dataset")
    cmp_datasets = _fetch_datasets()
    if not cmp_datasets:
        st.warning("No datasets stored. Upload a dataset first in **Run Analysis**.")
        st.stop()

    cmp_tf_mode = st.radio(
        "Timeframe Mode",
        ["Single Timeframe", "Multi-Timeframe"],
        horizontal=True,
        key="cmp_tf_mode",
    )

    cmp_ds_by_id = {d["dataset_id"]: d for d in cmp_datasets}
    cmp_labels   = {d["dataset_id"]: _dataset_label(d) for d in cmp_datasets}
    cmp_ids      = [d["dataset_id"] for d in cmp_datasets]

    cmp_dataset_id:  str | None       = None
    cmp_dataset_ids: dict[str, str]   = {}
    cmp_structure_tf: str | None      = None
    cmp_trend_tf:    str | None       = None
    cmp_entry_tf:    str | None       = None
    cmp_primary_tf   = "H1"

    if cmp_tf_mode == "Single Timeframe":
        cmp_chosen_id = st.selectbox(
            "Dataset", cmp_ids, format_func=lambda x: cmp_labels.get(x, x), key="cmp_ds_sel"
        )
        cmp_dataset_id = cmp_chosen_id
        cmp_primary_tf = cmp_ds_by_id[cmp_chosen_id]["timeframe"]
    else:
        cm1, cm2, cm3 = st.columns(3)
        with cm1:
            st.markdown("**Trend TF** *(optional)*")
            cmp_tr = st.selectbox("Trend", ["None"] + cmp_ids,
                                  format_func=lambda x: "None" if x == "None" else cmp_labels.get(x, x),
                                  key="cmp_trend", label_visibility="collapsed")
        with cm2:
            st.markdown("**Structure TF** *(required)*")
            cmp_st = st.selectbox("Structure", cmp_ids,
                                  format_func=lambda x: cmp_labels.get(x, x),
                                  key="cmp_struct", label_visibility="collapsed")
        with cm3:
            st.markdown("**Entry TF** *(optional)*")
            cmp_en = st.selectbox("Entry", ["None"] + cmp_ids,
                                  format_func=lambda x: "None" if x == "None" else cmp_labels.get(x, x),
                                  key="cmp_entry", label_visibility="collapsed")
        cmp_structure_tf = cmp_ds_by_id[cmp_st]["timeframe"]
        cmp_primary_tf   = cmp_structure_tf
        cmp_dataset_ids[cmp_structure_tf] = cmp_st
        if cmp_tr and cmp_tr != "None":
            cmp_trend_tf = cmp_ds_by_id[cmp_tr]["timeframe"]
            cmp_dataset_ids[cmp_trend_tf] = cmp_tr
        if cmp_en and cmp_en != "None":
            cmp_entry_tf = cmp_ds_by_id[cmp_en]["timeframe"]
            cmp_dataset_ids[cmp_entry_tf] = cmp_en

    # ── Date range ────────────────────────────────────────────────────────────
    st.markdown("### 2. Date Range")
    cmp_all_fresh = _fetch_datasets()
    cmp_info_map  = {d["dataset_id"]: d for d in cmp_all_fresh}

    cmp_avail_starts: list = []
    cmp_avail_ends:   list = []

    cmp_source_ids = [cmp_dataset_id] if cmp_dataset_id else list(cmp_dataset_ids.values())
    for _did in cmp_source_ids:
        _d = cmp_info_map.get(_did, {})
        _s = _parse_date(_d.get("start_datetime", ""))
        _e = _parse_date(_d.get("end_datetime", ""))
        if _s:
            cmp_avail_starts.append(_s)
        if _e:
            cmp_avail_ends.append(_e)

    cmp_bt_start: str | None = None
    cmp_bt_end:   str | None = None

    if cmp_avail_starts and cmp_avail_ends:
        cmp_avail_start = max(cmp_avail_starts)
        cmp_avail_end   = min(cmp_avail_ends)
        ca1, ca2 = st.columns(2)
        ca1.info(f"**Available Start:** {cmp_avail_start}")
        ca2.info(f"**Available End:** {cmp_avail_end}")

        cmp_use_full = st.checkbox("Use Full Dataset Range", value=True, key="cmp_full_range")
        if cmp_use_full:
            cmp_bt_start = str(cmp_avail_start)
            cmp_bt_end   = str(cmp_avail_end)
        else:
            cd1, cd2 = st.columns(2)
            with cd1:
                cmp_start_val = st.date_input("Start Date", value=cmp_avail_start,
                                              min_value=cmp_avail_start, max_value=cmp_avail_end,
                                              key="cmp_start_inp")
            with cd2:
                cmp_end_val = st.date_input("End Date", value=cmp_avail_end,
                                            min_value=cmp_avail_start, max_value=cmp_avail_end,
                                            key="cmp_end_inp")
            if cmp_start_val >= cmp_end_val:
                st.error("❌ Start must be before End.")
                st.stop()
            cmp_bt_start = str(cmp_start_val)
            cmp_bt_end   = str(cmp_end_val)

    # ── Parameters ────────────────────────────────────────────────────────────
    st.markdown("### 3. Shared Parameters")
    cp1, cp2, cp3 = st.columns(3)
    cmp_risk_pct = cp1.number_input("Risk % / Trade", min_value=0.25, max_value=3.0,
                                    value=1.0, step=0.25, key="cmp_risk")
    cmp_rr       = cp2.number_input("RR Ratio", min_value=1.0, max_value=5.0,
                                    value=2.0, step=0.5, key="cmp_rr")
    cmp_lookback = cp3.number_input("Swing N", min_value=2, max_value=20,
                                    value=5, step=1, key="cmp_lookback")

    st.markdown("**Break & Retest Params** *(used only for that module)*")
    cbp1, cbp2 = st.columns(2)
    cmp_breakout_buf  = cbp1.number_input("Breakout Buffer", min_value=0.0, max_value=2.0,
                                          value=0.10, step=0.05, key="cmp_bb")
    cmp_retest_tol    = cbp2.number_input("Retest Tolerance", min_value=0.0, max_value=2.0,
                                          value=0.50, step=0.05, key="cmp_rt")

    cmp_name = st.text_input("Comparison Name (optional)",
                              placeholder="e.g. XAUUSD H1 — Q1 2024 Comparison",
                              key="cmp_name_input")

    cmp_modules_sel = st.multiselect(
        "Modules to compare",
        options=["liquidity_sweep", "break_retest"],
        default=["liquidity_sweep", "break_retest"],
        format_func=lambda x: {"liquidity_sweep": "Liquidity Sweep",
                                "break_retest": "Break & Retest"}.get(x, x),
        key="cmp_modules_sel",
    )

    # ── Run comparison ────────────────────────────────────────────────────────
    st.markdown("### 4. Run Comparison")
    run_cmp_btn = st.button("⚖️ Run Comparison", type="primary", key="run_cmp_btn",
                            disabled=not cmp_modules_sel)

    if run_cmp_btn:
        cmp_payload: dict = {
            "modules":          cmp_modules_sel,
            "comparison_name":  cmp_name or "Module Comparison",
            "analysis_mode":    "single" if cmp_tf_mode == "Single Timeframe" else "multi",
            "timeframe":        cmp_primary_tf,
            "risk_pct":         cmp_risk_pct,
            "rr":               cmp_rr,
            "lookback":         int(cmp_lookback),
            "breakout_buffer":  cmp_breakout_buf,
            "retest_tolerance": cmp_retest_tol,
            "backtest_start":   cmp_bt_start,
            "backtest_end":     cmp_bt_end,
            "analysis_sub_mode": "full_backtest",
            "goal_profile_id":  goal_profile_id,
        }
        if cmp_tf_mode == "Single Timeframe":
            cmp_payload["dataset_id"] = cmp_dataset_id
        else:
            cmp_payload["dataset_ids"]  = cmp_dataset_ids
            cmp_payload["structure_tf"] = cmp_structure_tf
            cmp_payload["trend_tf"]     = cmp_trend_tf
            cmp_payload["entry_tf"]     = cmp_entry_tf

        _t_cmp = time.perf_counter()
        with st.spinner("Running comparison — both modules on the same dataset…"):
            try:
                _cmp_result = svc.run_comparison(cmp_payload)
                st.session_state["cmp_result"] = _cmp_result
                _clear_data_cache()
                st.success("Comparison complete!")
            except svc.SvcError as _e:
                st.error(f"Comparison failed: {_e.message}")
        print(f"[perf] run_comparison: {(time.perf_counter()-_t_cmp)*1000:.0f}ms", flush=True)

    # ── Display results ───────────────────────────────────────────────────────
    _MOD_LABEL = {"liquidity_sweep": "Liquidity Sweep", "break_retest": "Break & Retest"}

    if "cmp_result" in st.session_state:
        cmp_data = st.session_state["cmp_result"]
        st.divider()
        st.markdown("### 📊 Comparison Table")

        cmp_table = cmp_data.get("comparison", [])
        if cmp_table:
            # Highlight goal statuses with colour
            cmp_df = pd.DataFrame(cmp_table)
            st.dataframe(cmp_df, use_container_width=True, hide_index=True)

            # Winner call-out
            valid = [r for r in cmp_table if "Error" not in r]
            if len(valid) >= 2:
                best_pf  = max(valid, key=lambda r: r.get("Profit Factor", 0))
                best_mr  = max(valid, key=lambda r: r.get("Monthly Return %", 0))
                best_dd  = min(valid, key=lambda r: r.get("Max Drawdown %", 999))
                st.markdown(
                    f"**Best Profit Factor:** {best_pf['Module']} `{best_pf.get('Profit Factor', 0):.2f}`  |  "
                    f"**Best Monthly Return:** {best_mr['Module']} `{best_mr.get('Monthly Return %', 0):.2f}%`  |  "
                    f"**Lowest Drawdown:** {best_dd['Module']} `{best_dd.get('Max Drawdown %', 0):.2f}%`"
                )

        # Export download
        cmp_export = cmp_data.get("export_path", "")
        if cmp_export:
            st.caption(f"Saved: `{cmp_export}`")

        if cmp_table:
            cmp_csv = pd.DataFrame(cmp_table).to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Download Comparison CSV",
                data=cmp_csv,
                file_name="module_comparison.csv",
                mime="text/csv",
                key="dl_cmp_csv",
            )

        # Any module errors
        for mod_id, err in cmp_data.get("errors", {}).items():
            st.error(f"❌ {_MOD_LABEL.get(mod_id, mod_id)}: {err}")

        # Per-module detail
        st.divider()
        st.markdown("### 🔍 Per-Module Results")
        for mod_id, mod_result in cmp_data.get("results", {}).items():
            with st.expander(f"📈 {_MOD_LABEL.get(mod_id, mod_id)} — full results", expanded=False):
                _render_results(mod_result, cmp_risk_pct, label=_MOD_LABEL.get(mod_id, mod_id), chart_key_prefix=f"cmp_{mod_id}")


# ═════════════════════════ TAB 7 — GOAL PROFILES ══════════════════════════════
with tab7:
    st.subheader("🎯 Goal Configuration")
    st.caption(
        "Create and manage goal profiles. Each profile defines the 4 evaluation thresholds used "
        "when running analysis. Select a profile in the sidebar to apply it to your next run."
    )

    gp_all = _fetch_goal_profiles()

    # ── Current Profiles Table ────────────────────────────────────────────────
    st.markdown("### Current Profiles")
    if gp_all:
        gp_rows = []
        for p in gp_all:
            gp_rows.append({
                "Default":          "⭐ Yes" if p.get("is_default") else "—",
                "Profile Name":     p["profile_name"],
                "Monthly Return ≥": f"{p['monthly_return_target']}%",
                "Daily DD ≤":       f"{p['daily_drawdown_target']}%",
                "Monthly DD ≤":     f"{p['monthly_drawdown_target']}%",
                "Profit Factor ≥":  f"{p['profit_factor_target']}",
                "_id":              p["id"],
            })
        gp_df = pd.DataFrame(gp_rows)
        st.dataframe(gp_df.drop(columns=["_id"]), use_container_width=True, hide_index=True)
        st.caption(f"{len(gp_all)} profile(s) · PASS = all 4 met · WATCHLIST = 3/4 · FAIL = <3/4")
    else:
        st.warning("No profiles found — backend may be offline or database not yet initialised.")

    st.divider()

    # ── Profile Actions ───────────────────────────────────────────────────────
    st.markdown("### Manage Profiles")

    if gp_all:
        gp_sel_idx = st.selectbox(
            "Select profile to manage",
            range(len(gp_all)),
            format_func=lambda i: (
                f"{'⭐ ' if gp_all[i].get('is_default') else ''}{gp_all[i]['profile_name']}"
            ),
            key="gp_manage_sel",
        )
        gp_sel = gp_all[gp_sel_idx]
        gp_sel_id = gp_sel["id"]

        gpa_col, gpb_col, gpc_col = st.columns(3)

        # ── Set as Default ────────────────────────────────────────────────────
        with gpa_col:
            if gp_sel.get("is_default"):
                st.button("⭐ Already Default", disabled=True, key="gp_set_default_btn")
            elif st.button("⭐ Set as Default", key="gp_set_default_btn", type="secondary"):
                try:
                    svc.set_default_goal_profile(gp_sel_id)
                    st.success(f"'{gp_sel['profile_name']}' is now the default profile.")
                    _clear_data_cache()
                    st.rerun()
                except svc.SvcError as _e:
                    st.error(f"Failed: {_e.message}")

        # ── Delete ────────────────────────────────────────────────────────────
        with gpb_col:
            if gp_sel.get("is_default"):
                st.button("🗑️ Delete", disabled=True, key="gp_delete_btn",
                          help="Cannot delete the default profile. Set another as default first.")
            elif st.button("🗑️ Delete", key="gp_delete_btn", type="secondary"):
                st.session_state["_confirm_delete_gp"] = gp_sel_id

        if st.session_state.get("_confirm_delete_gp") == gp_sel_id:
            st.warning(f"Delete profile **{gp_sel['profile_name']}**? This cannot be undone.")
            dgc1, dgc2 = st.columns(2)
            if dgc1.button("✅ Yes, delete", type="primary", key="gp_del_confirm"):
                try:
                    svc.delete_goal_profile(gp_sel_id)
                    st.success("Profile deleted.")
                    st.session_state.pop("_confirm_delete_gp", None)
                    _clear_data_cache()
                    st.rerun()
                except svc.SvcError as _e:
                    st.error(f"Delete failed: {_e.message}")
            if dgc2.button("✗ Cancel", key="gp_del_cancel"):
                st.session_state.pop("_confirm_delete_gp", None)

        # ── Edit ─────────────────────────────────────────────────────────────
        with st.expander(f"✏️ Edit — {gp_sel['profile_name']}", expanded=False):
            with st.form(f"edit_gp_{gp_sel_id}", border=False):
                ep_name = st.text_input("Profile Name", value=gp_sel["profile_name"])
                ep1, ep2 = st.columns(2)
                ep_mr  = ep1.number_input("Monthly Return ≥ (%)",
                                          min_value=0.1, max_value=100.0,
                                          value=float(gp_sel["monthly_return_target"]), step=0.5,
                                          key=f"ep_mr_{gp_sel_id}")
                ep_ddd = ep2.number_input("Daily Drawdown ≤ (%)",
                                          min_value=0.1, max_value=100.0,
                                          value=float(gp_sel["daily_drawdown_target"]), step=0.5,
                                          key=f"ep_ddd_{gp_sel_id}")
                ep3, ep4 = st.columns(2)
                ep_mdd = ep3.number_input("Monthly Drawdown ≤ (%)",
                                          min_value=0.1, max_value=100.0,
                                          value=float(gp_sel["monthly_drawdown_target"]), step=0.5,
                                          key=f"ep_mdd_{gp_sel_id}")
                ep_pf  = ep4.number_input("Profit Factor ≥",
                                          min_value=0.1, max_value=100.0,
                                          value=float(gp_sel["profit_factor_target"]), step=0.1,
                                          key=f"ep_pf_{gp_sel_id}")
                if st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True):
                    if not ep_name.strip():
                        st.error("Profile name cannot be empty.")
                    else:
                        try:
                            svc.update_goal_profile(gp_sel_id, {
                                "profile_name":            ep_name.strip(),
                                "monthly_return_target":   ep_mr,
                                "daily_drawdown_target":   ep_ddd,
                                "monthly_drawdown_target": ep_mdd,
                                "profit_factor_target":    ep_pf,
                            })
                            st.success("Profile updated.")
                            _clear_data_cache()
                            st.rerun()
                        except svc.SvcError as _e:
                            st.error(f"Update failed: {_e.message}")

    # ── Create New Profile ────────────────────────────────────────────────────
    st.divider()
    st.markdown("### Create New Profile")
    st.caption(
        "Examples: **Conservative** (low DD, modest return) · "
        "**Aggressive** (high return target) · "
        "**Prop Firm** (strict daily DD < 2%) · "
        "**Personal Account** (custom thresholds)"
    )

    with st.form("create_goal_profile", border=True):
        np_name = st.text_input(
            "Profile Name",
            placeholder="e.g. Prop Firm, Personal Account, Conservative…",
        )
        cp1, cp2 = st.columns(2)
        np_mr  = cp1.number_input("Monthly Return ≥ (%)",  min_value=0.1, max_value=100.0, value=5.0,  step=0.5)
        np_ddd = cp2.number_input("Daily Drawdown ≤ (%)",  min_value=0.1, max_value=100.0, value=4.0,  step=0.5)
        cp3, cp4 = st.columns(2)
        np_mdd = cp3.number_input("Monthly Drawdown ≤ (%)", min_value=0.1, max_value=100.0, value=12.0, step=0.5)
        np_pf  = cp4.number_input("Profit Factor ≥",        min_value=0.1, max_value=100.0, value=1.5,  step=0.1)
        np_default = st.checkbox("Set as default profile")

        if st.form_submit_button("➕ Create Profile", type="primary", use_container_width=True):
            if not np_name.strip():
                st.error("Profile name cannot be empty.")
            else:
                try:
                    svc.create_goal_profile({
                        "profile_name":            np_name.strip(),
                        "monthly_return_target":   np_mr,
                        "daily_drawdown_target":   np_ddd,
                        "monthly_drawdown_target": np_mdd,
                        "profit_factor_target":    np_pf,
                        "is_default":              np_default,
                    })
                    st.success(f"Profile '{np_name.strip()}' created.")
                    _clear_data_cache()
                    st.rerun()
                except svc.SvcError as exc:
                    st.error(f"Create failed: {exc.message}")

    # ── How PASS/WATCHLIST/FAIL Works ─────────────────────────────────────────
    st.divider()
    st.markdown("### How PASS / WATCHLIST / FAIL Works")
    st.markdown("""
Each analysis run is evaluated against the 4 metrics in the active goal profile:

| Metric | Direction | Notes |
|---|---|---|
| Monthly Return | Actual ≥ Target | Average monthly % gain over the backtest |
| Daily Drawdown | Actual ≤ Target | Worst single-day equity loss |
| Monthly Drawdown | Actual ≤ Target | Worst single-month equity loss |
| Profit Factor | Actual ≥ Target | Gross profit ÷ gross loss (in R) |

**Composite Status:**
- **PASS** — All 4 metrics meet their targets
- **WATCHLIST** — 3 of 4 metrics pass (75 %)
- **FAIL** — Fewer than 3 of 4 metrics pass (< 75 %)
""")
    st.info(
        "Old research runs (before Goal Profiles were added) used the legacy 3-metric evaluation "
        "and are stored as **Legacy Goal Profile** in the research history."
    )
