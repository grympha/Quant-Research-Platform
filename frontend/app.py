"""
XAUUSD Quant Research Platform — Streamlit Dashboard
=====================================================
Phase 1 — Liquidity Sweep analysis

Workflow:
  Step 1 · Upload MT5 OHLCV CSV
  Step 2 · Configure parameters (sidebar)
  Step 3 · Run Analysis
  Step 4 · Review results: metrics, equity curve, trade list, goal report
"""

from __future__ import annotations

import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────
API = "http://localhost:8000"
GOAL_MONTHLY_MIN = 3.0
GOAL_MONTHLY_MAX = 5.0
GOAL_DD_LIMIT = 4.0
GOAL_PF_MIN = 1.5

st.set_page_config(
    page_title="XAUUSD Quant Research",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .metric-pass  { color: #26a69a; font-weight: 700; }
    .metric-fail  { color: #ef5350; font-weight: 700; }
    .metric-warn  { color: #ffa726; font-weight: 700; }
    .badge-pass   { background:#1b5e20; color:#a5d6a7; padding:4px 14px;
                    border-radius:20px; font-weight:700; font-size:1.1rem; }
    .badge-fail   { background:#b71c1c; color:#ef9a9a; padding:4px 14px;
                    border-radius:20px; font-weight:700; font-size:1.1rem; }
    .badge-insuf  { background:#37474f; color:#b0bec5; padding:4px 14px;
                    border-radius:20px; font-weight:700; font-size:1.1rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Header ────────────────────────────────────────────────────────────────────
col_h1, col_h2 = st.columns([3, 1])
with col_h1:
    st.title("📊 XAUUSD Quant Research Platform")
    st.caption("Phase 1 — Liquidity Sweep Backtesting")
with col_h2:
    try:
        r = requests.get(f"{API}/health", timeout=2)
        if r.status_code == 200:
            st.success("API Online", icon="🟢")
        else:
            st.warning("API Error", icon="🟡")
    except Exception:
        st.error("API Offline — start uvicorn", icon="🔴")

st.divider()

# ── Sidebar — Analysis Configuration ─────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    st.subheader("Module")
    module = st.selectbox(
        "Strategy Module",
        options=["liquidity_sweep"],
        format_func=lambda x: {"liquidity_sweep": "Liquidity Sweep"}[x],
        label_visibility="collapsed",
    )

    st.subheader("Timeframe")
    timeframe = st.selectbox(
        "Timeframe",
        options=["M15", "M30", "H1", "H4", "D1"],
        index=2,
        label_visibility="collapsed",
    )

    st.subheader("Risk per Trade")
    risk_pct = st.slider(
        "Risk %", min_value=0.25, max_value=3.0, value=1.0, step=0.25,
        format="%.2f%%", label_visibility="collapsed"
    )

    st.subheader("Risk : Reward")
    rr = st.slider(
        "RR", min_value=1.0, max_value=5.0, value=2.0, step=0.5,
        format="%.1fR", label_visibility="collapsed"
    )

    st.subheader("Lookback Periods")
    lookback = st.slider(
        "Lookback", min_value=5, max_value=50, value=20, step=5,
        label_visibility="collapsed"
    )

    st.divider()
    st.markdown("**🎯 Target Goals**")
    st.markdown(f"- Monthly Return: **{GOAL_MONTHLY_MIN}%–{GOAL_MONTHLY_MAX}%**")
    st.markdown(f"- Max Drawdown: **< {GOAL_DD_LIMIT}%**")
    st.markdown(f"- Profit Factor: **> {GOAL_PF_MIN}**")


# ── Step 1 — Upload ───────────────────────────────────────────────────────────
st.subheader("Step 1 — Upload MT5 OHLCV CSV")
st.caption("Export from MT5: File → Save As → CSV  |  Format: `Date,Time,Open,High,Low,Close,Volume`")

uploaded_file = st.file_uploader(
    "Choose a .csv file", type=["csv"], label_visibility="collapsed"
)

if uploaded_file is not None:
    if st.session_state.get("_last_file") != uploaded_file.name:
        # New file — reset state
        for key in ("upload_id", "upload_info", "analysis"):
            st.session_state.pop(key, None)
        st.session_state["_last_file"] = uploaded_file.name

    if "upload_id" not in st.session_state:
        with st.spinner("Uploading and parsing CSV…"):
            try:
                resp = requests.post(
                    f"{API}/api/v1/upload",
                    files={"file": (uploaded_file.name, uploaded_file.getvalue(), "text/csv")},
                    timeout=30,
                )
            except requests.exceptions.ConnectionError:
                st.error(
                    "Cannot reach the backend.  \n"
                    "Run: `uvicorn backend.main:app --reload` in a separate terminal."
                )
                st.stop()

        if resp.status_code == 200:
            info = resp.json()
            st.session_state["upload_id"] = info["upload_id"]
            st.session_state["upload_info"] = info
        else:
            detail = resp.json().get("detail", resp.text)
            st.error(f"Upload failed ({resp.status_code}): {detail}")
            st.stop()

    info = st.session_state["upload_info"]
    st.success(
        f"✅  **{info['filename']}** — {info['rows']:,} bars  "
        f"({info['start'][:10]} → {info['end'][:10]})"
    )
    with st.expander("Data preview (first 5 rows)"):
        st.dataframe(pd.DataFrame(info["preview"]), use_container_width=True)


# ── Step 2 — Run Analysis ─────────────────────────────────────────────────────
if "upload_id" in st.session_state:
    st.divider()
    st.subheader("Step 2 — Run Analysis")

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        run_btn = st.button("▶  Run Analysis", type="primary", use_container_width=True)
    with col_info:
        st.caption(
            f"Module: **{module.replace('_', ' ').title()}**  |  "
            f"Timeframe: **{timeframe}**  |  "
            f"Risk: **{risk_pct}%**  |  RR: **{rr}R**  |  Lookback: **{lookback}**"
        )

    if run_btn:
        with st.spinner("Running Liquidity Sweep analysis…"):
            try:
                resp = requests.post(
                    f"{API}/api/v1/analyze",
                    json={
                        "upload_id": st.session_state["upload_id"],
                        "module": module,
                        "timeframe": timeframe,
                        "risk_pct": risk_pct,
                        "rr": rr,
                        "lookback": lookback,
                    },
                    timeout=120,
                )
            except requests.exceptions.ConnectionError:
                st.error("Lost connection to backend.")
                st.stop()

        if resp.status_code == 200:
            st.session_state["analysis"] = resp.json()
        else:
            detail = resp.json().get("detail", resp.text)
            st.error(f"Analysis failed ({resp.status_code}): {detail}")
            st.stop()


# ── Step 3 — Results ──────────────────────────────────────────────────────────
if "analysis" in st.session_state:
    result = st.session_state["analysis"]
    rpt = result["report"]
    trades = result["trades"]

    st.divider()
    st.subheader("Step 3 — Research Results")

    # ── Goal status badge ─────────────────────────────────────────────────────
    status = rpt["goal_status"]
    badge_class = {"PASS": "badge-pass", "FAIL": "badge-fail"}.get(status, "badge-insuf")
    st.markdown(
        f"**Strategy Goal Status:** &nbsp;&nbsp;"
        f'<span class="{badge_class}">{status}</span>',
        unsafe_allow_html=True,
    )
    st.write("")

    # ── 7 Key Metrics ─────────────────────────────────────────────────────────
    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)

    m1.metric("Total Trades", rpt["total_trades"])
    m2.metric("Win Rate", f"{rpt['win_rate']:.1f}%")
    m3.metric("Profit Factor", f"{rpt['profit_factor']:.2f}")
    m4.metric("Net R", f"{rpt['net_r']:+.2f}R")

    _mr = rpt["monthly_return"]
    m5.metric(
        "Monthly Return",
        f"{_mr:.2f}%",
        delta="On target" if GOAL_MONTHLY_MIN <= _mr <= GOAL_MONTHLY_MAX else "Off target",
        delta_color="normal" if GOAL_MONTHLY_MIN <= _mr <= GOAL_MONTHLY_MAX else "inverse",
    )

    _dd = rpt["max_drawdown"]
    m6.metric(
        "Max Drawdown",
        f"{_dd:.2f}%",
        delta="Within limit" if _dd < GOAL_DD_LIMIT else "Exceeded",
        delta_color="normal" if _dd < GOAL_DD_LIMIT else "inverse",
    )

    m7.metric("Open Trades", rpt.get("open_trades", 0))

    st.write("")

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab_eq, tab_trades, tab_goals, tab_hist = st.tabs(
        ["📈 Equity Curve", "📋 Trade List", "🎯 Goal Report", "🕒 History"]
    )

    # ── Equity Curve tab ──────────────────────────────────────────────────────
    with tab_eq:
        equity = rpt["equity_curve"]
        n = len(equity)
        color = "#26a69a" if equity[-1] >= 0 else "#ef5350"
        fill_color = "rgba(38,166,154,0.12)" if equity[-1] >= 0 else "rgba(239,83,80,0.12)"

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=list(range(n)),
                y=equity,
                mode="lines",
                line=dict(color=color, width=2),
                fill="tozeroy",
                fillcolor=fill_color,
                name="Equity",
            )
        )
        fig.add_hline(y=0, line_color="rgba(255,255,255,0.3)", line_dash="dash")
        fig.update_layout(
            title=f"Cumulative Account Return (%) — {rpt['total_trades']} closed trades",
            xaxis_title="Trade #",
            yaxis_title="Return (%)",
            template="plotly_dark",
            height=420,
            margin=dict(l=10, r=10, t=45, b=10),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        # Win/Loss distribution donut
        wins_n = rpt["win_trades"]
        losses_n = rpt["loss_trades"]
        opens_n = rpt.get("open_trades", 0)

        col_d1, col_d2 = st.columns([1, 2])
        with col_d1:
            fig2 = go.Figure(
                go.Pie(
                    labels=["Win", "Loss", "Open"],
                    values=[wins_n, losses_n, opens_n],
                    marker_colors=["#26a69a", "#ef5350", "#ffa726"],
                    hole=0.45,
                    textinfo="label+percent",
                )
            )
            fig2.update_layout(
                template="plotly_dark",
                height=280,
                margin=dict(l=0, r=0, t=30, b=0),
                title_text="Trade Distribution",
                showlegend=False,
            )
            st.plotly_chart(fig2, use_container_width=True)

        with col_d2:
            st.markdown("**Performance Summary**")
            rows = [
                ("Gross Profit (R)", f"+{rpt['gross_profit_r']:.2f}R"),
                ("Gross Loss (R)", f"-{rpt['gross_loss_r']:.2f}R"),
                ("Net R", f"{rpt['net_r']:+.2f}R"),
                ("Net Return", f"{rpt['net_r'] * risk_pct:.2f}%"),
                ("Monthly Return", f"{rpt['monthly_return']:.2f}%"),
                ("Max Drawdown", f"{rpt['max_drawdown']:.2f}%"),
                ("Profit Factor", f"{rpt['profit_factor']:.2f}"),
                ("Win Rate", f"{rpt['win_rate']:.1f}%"),
            ]
            tbl = pd.DataFrame(rows, columns=["Metric", "Value"])
            st.dataframe(tbl, use_container_width=True, hide_index=True)

    # ── Trade List tab ────────────────────────────────────────────────────────
    with tab_trades:
        if trades:
            df_trades = pd.DataFrame(trades)
            df_trades["result_display"] = df_trades["result"].map(
                {"win": "✅ Win", "loss": "❌ Loss", "open": "⏳ Open"}
            )
            df_trades["direction"] = df_trades["direction"].str.capitalize()

            display_cols = {
                "date": "Date",
                "time": "Time",
                "direction": "Direction",
                "entry": "Entry",
                "stop": "Stop",
                "target": "Target",
                "exit_price": "Exit",
                "result_display": "Result",
                "r_multiple": "R Multiple",
                "bars_held": "Bars Held",
            }
            st.dataframe(
                df_trades[list(display_cols.keys())].rename(columns=display_cols),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(f"{len(df_trades)} total signals  |  {wins_n} wins  |  {losses_n} losses  |  {opens_n} open")
        else:
            st.info("No trade signals detected in this dataset. Try reducing the Lookback value.")

    # ── Goal Report tab ───────────────────────────────────────────────────────
    with tab_goals:
        st.markdown("### Goal Evaluation")
        goal_detail = rpt.get("goal_detail", {})

        def _row(label: str, key: str):
            g = goal_detail.get(key, {})
            icon = "✅" if g.get("passed") else "❌"
            val = g.get("value", "N/A")
            tgt = g.get("target", "—")
            st.markdown(f"{icon} &nbsp; **{label}** — Actual: `{val}` &nbsp;|&nbsp; Target: `{tgt}`")

        _row("Monthly Return", "monthly_return")
        _row("Max Drawdown", "max_drawdown")
        _row("Profit Factor", "profit_factor")

        st.divider()
        st.markdown("### Parameter Snapshot")
        params = result.get("parameters", {})
        p_df = pd.DataFrame(
            [
                ("Module", result.get("module", "—").replace("_", " ").title()),
                ("Timeframe", result.get("timeframe", "—")),
                ("Risk per Trade", f"{params.get('risk_pct', risk_pct)}%"),
                ("RR Ratio", f"{params.get('rr', rr)}R"),
                ("Lookback", params.get("lookback", lookback)),
            ],
            columns=["Parameter", "Value"],
        )
        st.dataframe(p_df, use_container_width=True, hide_index=True)

    # ── History tab ───────────────────────────────────────────────────────────
    with tab_hist:
        try:
            hist_resp = requests.get(f"{API}/api/v1/history?limit=20", timeout=5)
            if hist_resp.status_code == 200:
                analyses = hist_resp.json().get("analyses", [])
                if analyses:
                    import json as _json

                    rows = []
                    for a in analyses:
                        r = _json.loads(a["result"]) if isinstance(a["result"], str) else a["result"]
                        rows.append(
                            {
                                "Date": a["created_at"][:19].replace("T", " "),
                                "File": a["filename"],
                                "Module": a["module"].replace("_", " ").title(),
                                "TF": a["timeframe"],
                                "Risk%": a["risk_pct"],
                                "RR": a["rr"],
                                "Trades": r.get("total_trades", 0),
                                "Win%": r.get("win_rate", 0),
                                "PF": r.get("profit_factor", 0),
                                "Net R": r.get("net_r", 0),
                                "Monthly%": r.get("monthly_return", 0),
                                "DD%": r.get("max_drawdown", 0),
                                "Status": r.get("goal_status", "—"),
                            }
                        )
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                else:
                    st.info("No previous analyses found.")
        except Exception:
            st.warning("Could not load history.")
