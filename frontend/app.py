"""
XAUUSD Quant Research Platform — Streamlit Dashboard  (Phase 2)
================================================================
Phase 2 additions:
  • CSV validation report shown after upload
  • Swing Strength parameter replaces generic Lookback
  • Monthly performance bar chart
  • PASS / WATCHLIST / FAIL goal cards
  • Export paths displayed after analysis
"""

from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

API = "http://localhost:8000"

# ── Goal thresholds (mirrors core/report.py) ──────────────────────────────────
GOAL_MR_MIN  = 3.0
GOAL_MR_MAX  = 5.0
GOAL_DD_LIM  = 4.0
GOAL_PF_MIN  = 1.5

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="XAUUSD Quant Research",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* Goal status badges */
.badge { padding: 4px 16px; border-radius: 20px; font-weight: 700;
         font-size: 1.05rem; display: inline-block; }
.badge-pass      { background:#1b5e20; color:#a5d6a7; }
.badge-watchlist { background:#e65100; color:#ffe0b2; }
.badge-fail      { background:#b71c1c; color:#ef9a9a; }
.badge-insuf     { background:#37474f; color:#b0bec5; }

/* Goal card rows */
.goal-pass  { border-left: 4px solid #26a69a; padding-left: 10px; }
.goal-watch { border-left: 4px solid #ffa726; padding-left: 10px; }
.goal-fail  { border-left: 4px solid #ef5350; padding-left: 10px; }
</style>
""", unsafe_allow_html=True)

# ── Header ─────────────────────────────────────────────────────────────────────
col_t, col_s = st.columns([3, 1])
with col_t:
    st.title("📊 XAUUSD Quant Research Platform")
    st.caption("Phase 2 — Real Liquidity Sweep Backtesting")
with col_s:
    try:
        r = requests.get(f"{API}/health", timeout=2)
        info = r.json()
        st.success(f"API v{info.get('version','?')}  🟢", icon=None)
    except Exception:
        st.error("API offline — run uvicorn", icon="🔴")

st.divider()

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    st.subheader("Module")
    module = st.selectbox(
        "module", ["liquidity_sweep"],
        format_func=lambda x: "Liquidity Sweep",
        label_visibility="collapsed",
    )

    st.subheader("Timeframe")
    timeframe = st.selectbox(
        "tf", ["M15", "M30", "H1", "H4", "D1"],
        index=2, label_visibility="collapsed",
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


# ── Step 1 — Upload ────────────────────────────────────────────────────────────
st.subheader("Step 1 — Upload MT5 OHLCV CSV")
st.caption(
    "Drag & drop one **or more** CSV files — they will be merged into one dataset.  \n"
    "Format: `Date,Time,Open,High,Low,Close,Volume`  |  Export from MT5: File → Save As → CSV"
)

uploaded_files = st.file_uploader(
    "csv",
    type=["csv"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)

# Detect when the set of selected files changes and reset state
if uploaded_files:
    current_key = frozenset(f.name for f in uploaded_files)
    if st.session_state.get("_last_files") != current_key:
        for k in ("upload_id", "upload_info", "analysis"):
            st.session_state.pop(k, None)
        st.session_state["_last_files"] = current_key

    if "upload_id" not in st.session_state:
        label = (
            f"Uploading {len(uploaded_files)} file(s) and merging dataset…"
            if len(uploaded_files) > 1
            else "Uploading and validating CSV…"
        )
        with st.spinner(label):
            try:
                parts = [
                    ("files", (f.name, f.getvalue(), "text/csv"))
                    for f in uploaded_files
                ]
                resp = requests.post(
                    f"{API}/api/v1/upload-multiple",
                    files=parts,
                    timeout=60,
                )
            except requests.exceptions.ConnectionError:
                st.error(
                    "Cannot reach the backend.  \n"
                    "Run: `uvicorn backend.main:app --reload` in a separate terminal."
                )
                st.stop()

        if resp.status_code == 200:
            info = resp.json()
            st.session_state["upload_id"]   = info["upload_id"]
            st.session_state["upload_info"] = info
        else:
            body   = resp.json() if resp.headers.get("content-type", "").startswith("application") else {}
            detail = body.get("detail", resp.text)
            st.error(f"Upload failed ({resp.status_code}): {detail}")
            st.stop()

if "upload_info" in st.session_state:
    info = st.session_state["upload_info"]
    val  = info.get("validation", {})
    n_files = info.get("file_count", 1)

    # ── Combined dataset summary ──────────────────────────────────────────────
    if n_files > 1:
        st.success(
            f"✅  **{n_files} files merged** — {info['rows']:,} bars total  "
            f"({info['start'][:10]} → {info['end'][:10]})"
        )
        # Per-file breakdown table
        with st.expander(f"📂 Files included ({n_files})", expanded=True):
            per_file = info.get("files", [])
            file_df  = pd.DataFrame(per_file).rename(columns={
                "filename": "File",
                "rows":     "Bars",
                "start":    "Start",
                "end":      "End",
            })
            st.dataframe(file_df, use_container_width=True, hide_index=True)
    else:
        st.success(
            f"✅  **{info['filename']}** — {info['rows']:,} bars  "
            f"({info['start'][:10]} → {info['end'][:10]})"
        )

    # ── Validation report ─────────────────────────────────────────────────────
    with st.expander("📋 Dataset Validation Report", expanded=bool(val.get("warnings"))):
        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("Total bars",  f"{val.get('row_count', info['rows']):,}")
            st.metric("Date range",  val.get("date_range", "—"))
        with col_b:
            errs  = val.get("errors", [])
            warns = val.get("warnings", [])
            if errs:
                for e in errs:
                    st.error(f"❌ {e}")
            elif warns:
                for w in warns:
                    st.warning(f"⚠️  {w}")
            else:
                st.success("All checks passed.")

    with st.expander("Data preview (first 5 rows of combined dataset)"):
        st.dataframe(pd.DataFrame(info["preview"]), use_container_width=True)


# ── Step 2 — Run Analysis ──────────────────────────────────────────────────────
if "upload_id" in st.session_state:
    st.divider()
    st.subheader("Step 2 — Run Analysis")

    col_btn, col_desc = st.columns([1, 5])
    with col_btn:
        run_btn = st.button("▶  Run Analysis", type="primary", use_container_width=True)
    with col_desc:
        st.caption(
            f"Module: **Liquidity Sweep**  |  TF: **{timeframe}**  |  "
            f"Risk: **{risk_pct}%**  |  RR: **{rr}R**  |  Swing N: **{lookback}**"
        )

    if run_btn:
        with st.spinner("Running backtest — detecting swings, executing trades…"):
            try:
                resp = requests.post(
                    f"{API}/api/v1/analyze",
                    json={
                        "upload_id": st.session_state["upload_id"],
                        "module":    module,
                        "timeframe": timeframe,
                        "risk_pct":  risk_pct,
                        "rr":        rr,
                        "lookback":  lookback,
                    },
                    timeout=120,
                )
            except requests.exceptions.ConnectionError:
                st.error("Lost connection to backend.")
                st.stop()

        if resp.status_code == 200:
            st.session_state["analysis"] = resp.json()
            st.success("Analysis complete — scroll down for results.")
        else:
            body   = resp.json() if resp.headers.get("content-type","").startswith("application") else {}
            detail = body.get("detail", resp.text)
            st.error(f"Analysis failed ({resp.status_code}): {detail}")
            st.stop()


# ── Step 3 — Results ───────────────────────────────────────────────────────────
if "analysis" not in st.session_state:
    st.stop()

result = st.session_state["analysis"]
rpt    = result["report"]
trades = result["trades"]
params = result.get("parameters", {})

st.divider()
st.subheader("Step 3 — Research Results")

# ── Overall goal badge ─────────────────────────────────────────────────────────
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

# ── 7 headline metrics ─────────────────────────────────────────────────────────
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

# ── Export notice ──────────────────────────────────────────────────────────────
exports = result.get("exports", {})
if exports:
    with st.expander("📁 Exported CSVs"):
        st.code(exports.get("trade_log", ""), language=None)
        st.code(exports.get("research_summary", ""), language=None)

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_eq, tab_monthly, tab_trades, tab_goals, tab_hist = st.tabs([
    "📈 Equity Curve",
    "📅 Monthly Performance",
    "📋 Trade List",
    "🎯 Goal Report",
    "🕒 History",
])


# ────────────────────────────── Equity Curve ──────────────────────────────────
with tab_eq:
    equity  = rpt["equity_curve"]
    n_pts   = len(equity)
    pos_col = "#26a69a"
    neg_col = "#ef5350"
    line_c  = pos_col if equity[-1] >= 0 else neg_col
    fill_c  = "rgba(38,166,154,0.10)" if equity[-1] >= 0 else "rgba(239,83,80,0.10)"

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=list(range(n_pts)), y=equity,
        mode="lines", line=dict(color=line_c, width=2),
        fill="tozeroy", fillcolor=fill_c, name="Equity",
    ))
    fig_eq.add_hline(y=0, line_color="rgba(255,255,255,0.25)", line_dash="dash")

    # Drawdown shading
    peak = 0.0
    for idx, val in enumerate(equity):
        if val > peak:
            peak = val

    fig_eq.update_layout(
        title=f"Cumulative Account Return (%) — {rpt['total_trades']} closed trades",
        xaxis_title="Trade #", yaxis_title="Return (%)",
        template="plotly_dark", height=400,
        margin=dict(l=10, r=10, t=45, b=10), showlegend=False,
    )
    st.plotly_chart(fig_eq, use_container_width=True)

    # Win/Loss donut + summary table side by side
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


# ─────────────────────────── Monthly Performance ──────────────────────────────
with tab_monthly:
    monthly = rpt.get("monthly_breakdown", [])

    if not monthly:
        st.info("No closed trades to build a monthly breakdown.")
    else:
        months      = [m["month"]      for m in monthly]
        ret_pct     = [m["return_pct"] for m in monthly]
        bar_colors  = [pos_col if v >= 0 else neg_col for v in ret_pct]

        fig_mo = go.Figure()
        fig_mo.add_trace(go.Bar(
            x=months, y=ret_pct,
            marker_color=bar_colors,
            text=[f"{v:+.1f}%" for v in ret_pct],
            textposition="outside",
            name="Monthly Return %",
        ))
        # Target band
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

        # Monthly table
        mo_df = pd.DataFrame(monthly).rename(columns={
            "month":      "Month",
            "trades":     "Trades",
            "wins":       "Wins",
            "losses":     "Losses",
            "net_r":      "Net R",
            "return_pct": "Return %",
        })
        st.dataframe(mo_df, use_container_width=True, hide_index=True)


# ─────────────────────────────── Trade List ───────────────────────────────────
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


# ──────────────────────────────── Goal Report ─────────────────────────────────
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
    snap = [
        ("Symbol",      "XAUUSD"),
        ("Timeframe",   result.get("timeframe", "—")),
        ("Module",      result.get("module", "—").replace("_", " ").title()),
        ("Risk / Trade",f"{params.get('risk_pct', risk_pct)}%"),
        ("RR Ratio",    f"{params.get('rr', rr)}R"),
        ("Swing N",     params.get("lookback", lookback)),
    ]
    st.dataframe(
        pd.DataFrame(snap, columns=["Parameter", "Value"]),
        use_container_width=True, hide_index=True,
    )


# ─────────────────────────────────── History ──────────────────────────────────
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
                        "Date":        a["created_at"][:19].replace("T", " "),
                        "File":        a.get("filename", "—"),
                        "Module":      a["module"].replace("_", " ").title(),
                        "TF":          a["timeframe"],
                        "Risk%":       a["risk_pct"],
                        "RR":          a["rr"],
                        "Trades":      r.get("total_trades", 0),
                        "Win%":        r.get("win_rate", 0),
                        "PF":          r.get("profit_factor", 0),
                        "Net R":       r.get("net_r", 0),
                        "Monthly%":    r.get("monthly_return", 0),
                        "DD%":         r.get("max_drawdown", 0),
                        "Status":      r.get("goal_status", "—"),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.info("No previous analyses found.")
    except Exception:
        st.warning("Could not load history.")
