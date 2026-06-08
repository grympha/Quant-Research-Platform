"""
XAUUSD Quant Research Platform — Streamlit Dashboard (Phase 2 + Multi-TF)
==========================================================================
Upload one or more MT5 OHLCV CSV files, choose Single or Multi-Timeframe
analysis mode, run Liquidity Sweep backtesting, and review results.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

API = "http://localhost:8000"

# ── Goal thresholds (mirrors core/report.py) ──────────────────────────────────
GOAL_MR_MIN = 3.0
GOAL_MR_MAX = 5.0
GOAL_DD_LIM = 4.0
GOAL_PF_MIN = 1.5

# ── Timeframe ordering ────────────────────────────────────────────────────────
TF_ALL   = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
TF_ORDER = {tf: i for i, tf in enumerate(TF_ALL)}


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="XAUUSD Quant Research",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.badge { padding: 4px 16px; border-radius: 20px; font-weight: 700;
         font-size: 1.05rem; display: inline-block; }
.badge-pass      { background:#1b5e20; color:#a5d6a7; }
.badge-watchlist { background:#e65100; color:#ffe0b2; }
.badge-fail      { background:#b71c1c; color:#ef9a9a; }
.badge-insuf     { background:#37474f; color:#b0bec5; }

.goal-pass  { border-left: 4px solid #26a69a; padding-left: 10px; }
.goal-watch { border-left: 4px solid #ffa726; padding-left: 10px; }
.goal-fail  { border-left: 4px solid #ef5350; padding-left: 10px; }
</style>
""", unsafe_allow_html=True)


# ── Header ────────────────────────────────────────────────────────────────────
col_t, col_s = st.columns([3, 1])
with col_t:
    st.title("📊 XAUUSD Quant Research Platform")
    st.caption("Phase 2 + Multi-Timeframe — Real Liquidity Sweep Backtesting")
with col_s:
    try:
        r = requests.get(f"{API}/health", timeout=2)
        info = r.json()
        st.success(f"API v{info.get('version','?')}  🟢", icon=None)
    except Exception:
        st.error("API offline — run uvicorn", icon="🔴")

st.divider()


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    st.subheader("Module")
    module = st.selectbox(
        "module", ["liquidity_sweep"],
        format_func=lambda x: "Liquidity Sweep",
        label_visibility="collapsed",
    )

    st.subheader("Risk per Trade")
    risk_pct = st.slider(
        "risk", 0.25, 3.0, 1.0, 0.25,
        format="%.2f%%", label_visibility="collapsed",
    )

    st.subheader("Risk : Reward")
    rr = st.slider(
        "rr", 1.0, 5.0, 2.0, 0.5,
        format="%.1fR", label_visibility="collapsed",
    )

    st.subheader("Swing Strength (N bars each side)")
    lookback = st.slider(
        "lookback", 2, 20, 5, 1,
        label_visibility="collapsed",
        help=(
            "A bar is a swing high/low if it is the highest/lowest "
            "in a window of N bars on each side. "
            "Higher = only major pivots; lower = more sensitive swings."
        ),
    )

    st.divider()
    st.markdown("**🎯 Phase 2 Target Goals**")
    st.markdown(f"- Monthly Return &nbsp; **{GOAL_MR_MIN}%–{GOAL_MR_MAX}%**")
    st.markdown(f"- Max Drawdown &nbsp;&nbsp; **< {GOAL_DD_LIM}%**")
    st.markdown(f"- Profit Factor &nbsp;&nbsp;&nbsp;&nbsp; **> {GOAL_PF_MIN}**")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_tf(filename: str) -> str | None:
    """Auto-detect timeframe from an MT5 export filename (e.g. XAUUSD_H1_OHLCV.csv → H1)."""
    stem = re.sub(r"[\s\-\.]", "_", Path(filename).stem.upper())
    for tf in sorted(TF_ALL, key=len, reverse=True):  # longest first (M15 before M1)
        if re.search(r"(?:^|_)" + re.escape(tf) + r"(?:_|$)", stem):
            return tf
    return None


def _sorted_tfs(tfs: list[str]) -> list[str]:
    return sorted(tfs, key=lambda t: TF_ORDER.get(t, 99))


# ── Step 1 — Upload ───────────────────────────────────────────────────────────
st.subheader("Step 1 — Upload MT5 OHLCV CSV Files")
st.caption(
    "Drag & drop one or more CSV files.  \n"
    "Timeframe is auto-detected from the filename — expected format: "
    "`XAUUSD_H1_OHLCV.csv`, `XAUUSD_M15_OHLCV.csv`, etc.  \n"
    "CSV columns: `Date, Time, Open, High, Low, Close, Volume`"
)

uploaded_files = st.file_uploader(
    "csv",
    type=["csv"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)

# ── Detect file-set changes → reset downstream state ─────────────────────────
if uploaded_files:
    current_key = frozenset(f.name for f in uploaded_files)
    if st.session_state.get("_last_files") != current_key:
        for k in ("timeframe_uploads", "upload_status", "analysis"):
            st.session_state.pop(k, None)
        st.session_state["_last_files"] = current_key

    # ── Pre-upload: TF assignment table ──────────────────────────────────────
    if "timeframe_uploads" not in st.session_state:
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
                    f"tf_{f.name}",
                    TF_ALL,
                    index=TF_ALL.index(auto_tf),
                    key=f"tf_{f.name}",
                    label_visibility="collapsed",
                )
                row[2].markdown("✅")
            else:
                row[1].selectbox(
                    f"tf_{f.name}",
                    ["Select…"] + TF_ALL,
                    key=f"tf_{f.name}",
                    label_visibility="collapsed",
                )
                row[2].markdown("⚠️")

        has_unknown = any(
            st.session_state.get(f"tf_{f.name}", "Select…") == "Select…"
            for f in uploaded_files
        )
        if has_unknown:
            st.caption("⚠️  Some filenames don't contain a recognisable timeframe — assign them above.")

        if st.button(
            "Upload & Validate Files",
            type="primary",
            disabled=has_unknown,
        ):
            new_uploads: dict[str, str] = {}
            new_status: list[dict] = []

            with st.spinner("Uploading files…"):
                for f in uploaded_files:
                    tf = st.session_state.get(f"tf_{f.name}", "Select…")
                    if tf == "Select…":
                        continue

                    if tf in new_uploads:
                        new_status.append({
                            "Timeframe":  tf,
                            "Filename":   f.name,
                            "Rows":       "—",
                            "Start Date": "—",
                            "End Date":   "—",
                            "Status":     f"⚠️ Skipped (duplicate {tf})",
                        })
                        continue

                    try:
                        resp = requests.post(
                            f"{API}/api/v1/upload",
                            files={"file": (f.name, f.getvalue(), "text/csv")},
                            timeout=30,
                        )
                    except requests.exceptions.ConnectionError:
                        new_status.append({
                            "Timeframe":  tf, "Filename": f.name,
                            "Rows":       "—", "Start Date": "—", "End Date": "—",
                            "Status":     "❌ Connection error",
                        })
                        continue

                    if resp.status_code == 200:
                        info  = resp.json()
                        warns = len(info.get("validation", {}).get("warnings", []))
                        new_uploads[tf] = info["upload_id"]
                        new_status.append({
                            "Timeframe":  tf,
                            "Filename":   f.name,
                            "Rows":       f"{info['rows']:,}",
                            "Start Date": info["start"][:10],
                            "End Date":   info["end"][:10],
                            "Status":     "✅ OK" if warns == 0 else f"⚠️ {warns} warning(s)",
                        })
                    else:
                        try:
                            detail = resp.json().get("detail", "upload error")
                        except Exception:
                            detail = "upload error"
                        if isinstance(detail, dict):
                            detail = detail.get("message", str(detail))
                        new_status.append({
                            "Timeframe":  tf, "Filename": f.name,
                            "Rows":       "—", "Start Date": "—", "End Date": "—",
                            "Status":     f"❌ {detail}",
                        })

            st.session_state["timeframe_uploads"] = new_uploads
            st.session_state["upload_status"]     = new_status
            st.rerun()

elif not uploaded_files:
    st.info("Drop one or more MT5 OHLCV CSV files above to begin.")

# ── Post-upload: status table ─────────────────────────────────────────────────
if "upload_status" in st.session_state:
    st.dataframe(
        pd.DataFrame(st.session_state["upload_status"]),
        use_container_width=True,
        hide_index=True,
    )
    ok_tfs = list(st.session_state.get("timeframe_uploads", {}).keys())
    if ok_tfs:
        label = ", ".join(_sorted_tfs(ok_tfs))
        st.success(f"Ready for analysis — uploaded timeframes: **{label}**")


# ── Step 2 — Configure Analysis ───────────────────────────────────────────────
if not st.session_state.get("timeframe_uploads"):
    st.stop()

ok_tfs_sorted = _sorted_tfs(list(st.session_state["timeframe_uploads"].keys()))

st.divider()
st.subheader("Step 2 — Configure Analysis")

# ── Analysis mode ─────────────────────────────────────────────────────────────
analysis_mode = st.radio(
    "Analysis Mode",
    ["Single Timeframe", "Multi-Timeframe"],
    horizontal=True,
    help=(
        "**Single Timeframe** — run Liquidity Sweep on one uploaded timeframe.  \n"
        "**Multi-Timeframe** — specify trend / structure / entry timeframes for "
        "a layered, multi-context analysis."
    ),
)

req_upload_id = None
trend_tf = structure_tf = entry_tf = None
display_tf = ok_tfs_sorted[0]

if analysis_mode == "Single Timeframe":
    selected_tf = st.selectbox("Analysis Timeframe", ok_tfs_sorted)
    req_upload_id = st.session_state["timeframe_uploads"][selected_tf]
    display_tf    = selected_tf

else:  # Multi-Timeframe
    st.markdown(
        "Assign each uploaded timeframe to a role.  "
        "**Structure TF** is required — Liquidity Sweep detection runs here."
    )
    col_t, col_s, col_e = st.columns(3)

    with col_t:
        st.markdown("**Trend TF** *(optional)*")
        trend_sel = st.selectbox(
            "Trend TF",
            ["None"] + ok_tfs_sorted,
            key="sel_trend_tf",
            label_visibility="collapsed",
            help="Higher timeframe for market-direction context (e.g. H4, D1).",
        )
        trend_tf = None if trend_sel == "None" else trend_sel

    with col_s:
        st.markdown("**Structure TF** *(required)*")
        structure_tf = st.selectbox(
            "Structure TF",
            ok_tfs_sorted,
            key="sel_structure_tf",
            label_visibility="collapsed",
            help="Mid-range timeframe for swing detection (e.g. H1, M15).",
        )
        display_tf = structure_tf

    with col_e:
        st.markdown("**Entry TF** *(optional)*")
        entry_sel = st.selectbox(
            "Entry TF",
            ["None"] + ok_tfs_sorted,
            key="sel_entry_tf",
            label_visibility="collapsed",
            help="Finer timeframe for entry refinement (e.g. M5, M15).",
        )
        entry_tf = None if entry_sel == "None" else entry_sel

# ── Run button ────────────────────────────────────────────────────────────────
st.write("")
col_btn, col_desc = st.columns([1, 5])
with col_btn:
    run_btn = st.button("▶  Run Analysis", type="primary", use_container_width=True)
with col_desc:
    mode_label = "Single TF" if analysis_mode == "Single Timeframe" else "Multi-TF"
    st.caption(
        f"Module: **Liquidity Sweep** | Mode: **{mode_label}** | "
        f"Risk: **{risk_pct}%** | RR: **{rr}R** | Swing N: **{lookback}**"
    )

if run_btn:
    # ── Pre-run validation ────────────────────────────────────────────────────
    errors: list[str] = []
    uploaded_tfs = st.session_state["timeframe_uploads"]

    if analysis_mode == "Multi-Timeframe":
        if not structure_tf:
            errors.append(
                "Structure Timeframe is required for Multi-Timeframe analysis."
            )
        elif structure_tf not in uploaded_tfs:
            errors.append(
                f"Missing {structure_tf} data. "
                f"Please upload XAUUSD_{structure_tf}_OHLCV.csv."
            )

        if trend_tf and trend_tf not in uploaded_tfs:
            errors.append(
                f"Missing {trend_tf} data. "
                f"Please upload XAUUSD_{trend_tf}_OHLCV.csv."
            )

        if entry_tf and entry_tf not in uploaded_tfs:
            errors.append(
                f"Missing {entry_tf} data. "
                f"Please upload XAUUSD_{entry_tf}_OHLCV.csv."
            )

    if errors:
        for e in errors:
            st.error(f"❌ {e}")
        st.stop()

    # ── Build payload ─────────────────────────────────────────────────────────
    if analysis_mode == "Single Timeframe":
        payload: dict = {
            "upload_id":     req_upload_id,
            "analysis_mode": "single",
            "module":        module,
            "timeframe":     display_tf,
            "risk_pct":      risk_pct,
            "rr":            rr,
            "lookback":      lookback,
        }
    else:
        payload = {
            "analysis_mode":     "multi",
            "timeframe_uploads": st.session_state["timeframe_uploads"],
            "trend_tf":          trend_tf,
            "structure_tf":      structure_tf,
            "entry_tf":          entry_tf,
            "module":            module,
            "timeframe":         display_tf,
            "risk_pct":          risk_pct,
            "rr":                rr,
            "lookback":          lookback,
        }

    with st.spinner("Running backtest — detecting swings, executing trades…"):
        try:
            resp = requests.post(
                f"{API}/api/v1/analyze",
                json=payload,
                timeout=120,
            )
        except requests.exceptions.ConnectionError:
            st.error("Lost connection to backend.")
            st.stop()

    if resp.status_code == 200:
        st.session_state["analysis"] = resp.json()
        st.success("Analysis complete — scroll down for results.")
    else:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        st.error(f"Analysis failed ({resp.status_code}): {detail}")
        st.stop()


# ── Step 3 — Results ──────────────────────────────────────────────────────────
if "analysis" not in st.session_state:
    st.stop()

result = st.session_state["analysis"]
rpt    = result["report"]
trades = result["trades"]
params = result.get("parameters", {})

st.divider()
st.subheader("Step 3 — Research Results")

# ── Multi-TF context banner ───────────────────────────────────────────────────
if result.get("analysis_mode") == "multi":
    used = result.get("timeframes_used", [])
    parts = [f"Structure: **{result.get('structure_tf', '—')}**"]
    if result.get("trend_tf"):
        parts.insert(0, f"Trend: **{result['trend_tf']}**")
    if result.get("entry_tf"):
        parts.append(f"Entry: **{result['entry_tf']}**")
    st.info("Multi-Timeframe analysis — " + " | ".join(parts))

# ── Goal status badge ─────────────────────────────────────────────────────────
_STATUS_CLASS = {
    "PASS":              "badge-pass",
    "WATCHLIST":         "badge-watchlist",
    "FAIL":              "badge-fail",
    "INSUFFICIENT DATA": "badge-insuf",
}
status      = rpt["goal_status"]
badge_class = _STATUS_CLASS.get(status, "badge-insuf")
st.markdown(
    f"**Strategy Goal Status:** &nbsp;&nbsp;"
    f'<span class="badge {badge_class}">{status}</span>',
    unsafe_allow_html=True,
)
st.write("")

# ── 7 headline metrics ────────────────────────────────────────────────────────
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

c1.metric("Total Trades",  rpt["total_trades"])
c2.metric("Win Rate",      f"{rpt['win_rate']:.1f}%")
c3.metric("Profit Factor", f"{rpt['profit_factor']:.2f}")
c4.metric("Net R",         f"{rpt['net_r']:+.2f}R")

_mr = rpt["monthly_return"]
c5.metric(
    "Monthly Return",
    f"{_mr:.2f}%",
    delta="On target"   if GOAL_MR_MIN <= _mr <= GOAL_MR_MAX else "Off target",
    delta_color="normal" if GOAL_MR_MIN <= _mr <= GOAL_MR_MAX else "inverse",
)

_dd = rpt["max_drawdown"]
c6.metric(
    "Max Drawdown",
    f"{_dd:.2f}%",
    delta="Within limit" if _dd < GOAL_DD_LIM else "Limit exceeded",
    delta_color="normal" if _dd < GOAL_DD_LIM else "inverse",
)

c7.metric("Open / Expired", rpt.get("open_trades", 0))

st.write("")

# ── Export notice ─────────────────────────────────────────────────────────────
exports = result.get("exports", {})
if exports:
    with st.expander("📁 Exported CSVs"):
        st.code(exports.get("trade_log", ""), language=None)
        st.code(exports.get("research_summary", ""), language=None)

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_eq, tab_monthly, tab_trades, tab_goals, tab_hist = st.tabs([
    "📈 Equity Curve",
    "📅 Monthly Performance",
    "📋 Trade List",
    "🎯 Goal Report",
    "🕒 History",
])

pos_col = "#26a69a"
neg_col = "#ef5350"


# ─────────────────────────── Equity Curve ────────────────────────────────────
with tab_eq:
    equity = rpt["equity_curve"]
    n_pts  = len(equity)
    line_c = pos_col if equity[-1] >= 0 else neg_col
    fill_c = "rgba(38,166,154,0.10)" if equity[-1] >= 0 else "rgba(239,83,80,0.10)"

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=list(range(n_pts)), y=equity,
        mode="lines", line=dict(color=line_c, width=2),
        fill="tozeroy", fillcolor=fill_c, name="Equity",
    ))
    fig_eq.add_hline(y=0, line_color="rgba(255,255,255,0.25)", line_dash="dash")
    fig_eq.update_layout(
        title=f"Cumulative Account Return (%) — {rpt['total_trades']} closed trades",
        xaxis_title="Trade #", yaxis_title="Return (%)",
        template="plotly_dark", height=400,
        margin=dict(l=10, r=10, t=45, b=10), showlegend=False,
    )
    st.plotly_chart(fig_eq, use_container_width=True)

    col_d, col_tbl = st.columns([1, 2])
    with col_d:
        fig_pie = go.Figure(go.Pie(
            labels=["Win", "Loss", "Open"],
            values=[rpt["win_trades"], rpt["loss_trades"], rpt.get("open_trades", 0)],
            marker_colors=[pos_col, neg_col, "#ffa726"],
            hole=0.45, textinfo="label+percent",
        ))
        fig_pie.update_layout(
            template="plotly_dark", height=270,
            margin=dict(l=0, r=0, t=30, b=0),
            title_text="Trade Distribution", showlegend=False,
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    with col_tbl:
        summary_rows = [
            ("Gross Profit (R)",  f"+{rpt['gross_profit_r']:.2f}R"),
            ("Gross Loss (R)",    f"-{rpt['gross_loss_r']:.2f}R"),
            ("Net R",             f"{rpt['net_r']:+.2f}R"),
            ("Net Return",        f"{rpt['net_r'] * risk_pct:.2f}%"),
            ("Avg Monthly",       f"{rpt['monthly_return']:.2f}%"),
            ("Max Drawdown",      f"{rpt['max_drawdown']:.2f}%"),
            ("Profit Factor",     f"{rpt['profit_factor']:.2f}"),
            ("Win Rate",          f"{rpt['win_rate']:.1f}%"),
            ("Wins / Losses",     f"{rpt['win_trades']} / {rpt['loss_trades']}"),
        ]
        st.dataframe(
            pd.DataFrame(summary_rows, columns=["Metric", "Value"]),
            use_container_width=True, hide_index=True,
        )


# ──────────────────────── Monthly Performance ─────────────────────────────────
with tab_monthly:
    monthly = rpt.get("monthly_breakdown", [])

    if not monthly:
        st.info("No closed trades to build a monthly breakdown.")
    else:
        months     = [m["month"]      for m in monthly]
        ret_pct    = [m["return_pct"] for m in monthly]
        bar_colors = [pos_col if v >= 0 else neg_col for v in ret_pct]

        fig_mo = go.Figure()
        fig_mo.add_trace(go.Bar(
            x=months, y=ret_pct,
            marker_color=bar_colors,
            text=[f"{v:+.1f}%" for v in ret_pct],
            textposition="outside",
            name="Monthly Return %",
        ))
        fig_mo.add_hrect(
            y0=GOAL_MR_MIN, y1=GOAL_MR_MAX,
            fillcolor="rgba(38,166,154,0.08)",
            line_width=0,
            annotation_text="Target 3–5%",
            annotation_position="top right",
        )
        fig_mo.add_hline(y=0, line_color="rgba(255,255,255,0.25)", line_dash="dash")
        fig_mo.update_layout(
            title="Monthly Return (% of account)",
            xaxis_title="Month", yaxis_title="Return (%)",
            template="plotly_dark", height=380,
            margin=dict(l=10, r=10, t=45, b=10),
        )
        st.plotly_chart(fig_mo, use_container_width=True)

        mo_df = pd.DataFrame(monthly).rename(columns={
            "month":      "Month",
            "trades":     "Trades",
            "wins":       "Wins",
            "losses":     "Losses",
            "net_r":      "Net R",
            "return_pct": "Return %",
        })
        st.dataframe(mo_df, use_container_width=True, hide_index=True)


# ────────────────────────────── Trade List ────────────────────────────────────
with tab_trades:
    if trades:
        df_tr = pd.DataFrame(trades)
        df_tr["Result"] = df_tr["result"].map(
            {"win": "✅ Win", "loss": "❌ Loss", "open": "⏳ Open"}
        )
        df_tr["Dir"] = df_tr["direction"]

        col_map = {
            "date":        "Date",
            "time":        "Time",
            "Dir":         "Dir",
            "swept_level": "Swept Level",
            "entry":       "Entry",
            "sl":          "SL",
            "tp":          "TP",
            "exit_price":  "Exit",
            "Result":      "Result",
            "r_multiple":  "R",
            "bars_held":   "Bars",
        }
        disp = df_tr[[c for c in col_map if c in df_tr.columns]].rename(columns=col_map)
        st.dataframe(disp, use_container_width=True, hide_index=True)
        st.caption(
            f"{rpt['total_trades']} closed  |  "
            f"{rpt['win_trades']} wins  |  "
            f"{rpt['loss_trades']} losses  |  "
            f"{rpt.get('open_trades', 0)} open/expired"
        )
    else:
        st.info(
            "No trade signals detected.  \n"
            "Try a smaller **Swing Strength** value (sidebar) or a longer data range."
        )


# ──────────────────────────── Goal Report ─────────────────────────────────────
with tab_goals:
    goal_detail = rpt.get("goal_detail", {})

    _ICON  = {"PASS": "✅", "WATCHLIST": "⚠️", "FAIL": "❌"}
    _CLASS = {"PASS": "goal-pass", "WATCHLIST": "goal-watch", "FAIL": "goal-fail"}

    def _goal_card(label: str, key: str, hint: str = ""):
        g    = goal_detail.get(key, {})
        s    = g.get("status", "—")
        icon = _ICON.get(s, "❓")
        css  = _CLASS.get(s, "")
        val  = g.get("value", "N/A")
        tgt  = g.get("target", "—")
        st.markdown(
            f'<div class="{css}">'
            f"  {icon} &nbsp;<strong>{label}</strong><br>"
            f"  &nbsp;&nbsp;&nbsp;Actual: <code>{val}</code>&nbsp; Target: <code>{tgt}</code>"
            f"  {'<br>&nbsp;&nbsp;&nbsp;<em>' + hint + '</em>' if hint else ''}"
            f"</div><br>",
            unsafe_allow_html=True,
        )

    st.markdown("### Per-Metric Evaluation")
    _goal_card("Monthly Return",
               "monthly_return",
               "Average monthly % gain across the backtest period.")
    _goal_card("Max Drawdown",
               "max_drawdown",
               "Largest peak-to-trough decline in cumulative equity.")
    _goal_card("Profit Factor",
               "profit_factor",
               "Gross profit ÷ gross loss (R).")

    st.divider()
    st.markdown("### Parameter Snapshot")

    mode_display = result.get("analysis_mode", "single").replace("_", " ").title()
    tfs_used     = result.get("timeframes_used", [result.get("timeframe", "—")])

    snap = [
        ("Symbol",         "XAUUSD"),
        ("Analysis Mode",  mode_display),
        ("Timeframe(s)",   ", ".join(_sorted_tfs(tfs_used)) if tfs_used else "—"),
        ("Module",         result.get("module", "—").replace("_", " ").title()),
        ("Risk / Trade",   f"{params.get('risk_pct', risk_pct)}%"),
        ("RR Ratio",       f"{params.get('rr', rr)}R"),
        ("Swing N",        params.get("lookback", lookback)),
    ]
    if result.get("structure_tf"):
        snap.append(("Structure TF", result["structure_tf"]))
    if result.get("trend_tf"):
        snap.append(("Trend TF", result["trend_tf"]))
    if result.get("entry_tf"):
        snap.append(("Entry TF", result["entry_tf"]))

    st.dataframe(
        pd.DataFrame(snap, columns=["Parameter", "Value"]),
        use_container_width=True, hide_index=True,
    )


# ──────────────────────────────── History ─────────────────────────────────────
with tab_hist:
    try:
        hr = requests.get(f"{API}/api/v1/history?limit=20", timeout=5)
        if hr.status_code == 200:
            analyses = hr.json().get("analyses", [])
            if analyses:
                rows = []
                for a in analyses:
                    r = json.loads(a["result"]) if isinstance(a["result"], str) else a["result"]
                    rows.append({
                        "Date":     a["created_at"][:19].replace("T", " "),
                        "File":     a.get("filename", "—"),
                        "Module":   a["module"].replace("_", " ").title(),
                        "TF":       a["timeframe"],
                        "Risk%":    a["risk_pct"],
                        "RR":       a["rr"],
                        "Trades":   r.get("total_trades", 0),
                        "Win%":     r.get("win_rate", 0),
                        "PF":       r.get("profit_factor", 0),
                        "Net R":    r.get("net_r", 0),
                        "Monthly%": r.get("monthly_return", 0),
                        "DD%":      r.get("max_drawdown", 0),
                        "Status":   r.get("goal_status", "—"),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.info("No previous analyses found.")
    except Exception:
        st.warning("Could not load history.")
