"""
XAUUSD Quant Research Platform — Streamlit Dashboard v3
========================================================
Tab 1: Run Analysis         (upload new OR pick from library)
Tab 2: OHLCV Dataset Library
Tab 3: Research History
Tab 4: Export Center
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

# ── Goal thresholds ───────────────────────────────────────────────────────────
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

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="XAUUSD Quant Research",
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
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
col_t, col_s = st.columns([3, 1])
with col_t:
    st.title("📊 XAUUSD Quant Research Platform")
    st.caption("v3 — Dataset Library · Persistent Research History · Export Center")
with col_s:
    try:
        r = requests.get(f"{API}/health", timeout=2)
        info = r.json()
        st.success(f"API v{info.get('version','?')}  🟢", icon=None)
    except Exception:
        st.error("API offline — run uvicorn", icon="🔴")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Analysis Parameters")

    st.subheader("Module")
    module = st.selectbox(
        "module", ["liquidity_sweep"],
        format_func=lambda x: "Liquidity Sweep",
        label_visibility="collapsed",
    )

    st.subheader("Risk per Trade")
    risk_pct = st.slider("risk", 0.25, 3.0, 1.0, 0.25,
                         format="%.2f%%", label_visibility="collapsed")

    st.subheader("Risk : Reward")
    rr = st.slider("rr", 1.0, 5.0, 2.0, 0.5,
                   format="%.1fR", label_visibility="collapsed")

    st.subheader("Swing Strength (N bars each side)")
    lookback = st.slider("lookback", 2, 20, 5, 1,
                         label_visibility="collapsed",
                         help="Higher = only major pivots; lower = more sensitive swings.")

    st.divider()
    st.markdown("**🎯 Target Goals**")
    st.markdown(f"- Monthly Return &nbsp; **{GOAL_MR_MIN}%–{GOAL_MR_MAX}%**")
    st.markdown(f"- Max Drawdown &nbsp;&nbsp; **< {GOAL_DD_LIM}%**")
    st.markdown(f"- Profit Factor &nbsp;&nbsp;&nbsp;&nbsp; **> {GOAL_PF_MIN}**")


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


def _fetch_datasets() -> list[dict]:
    try:
        r = requests.get(f"{API}/api/v1/datasets", timeout=5)
        return r.json().get("datasets", []) if r.status_code == 200 else []
    except Exception:
        return []


def _fetch_research_runs(limit: int = 100) -> list[dict]:
    try:
        r = requests.get(f"{API}/api/v1/research?limit={limit}", timeout=5)
        return r.json().get("research_runs", []) if r.status_code == 200 else []
    except Exception:
        return []


pos_col = "#26a69a"
neg_col = "#ef5350"


# ── Results renderer (shared across tabs) ─────────────────────────────────────

def _render_results(result: dict, risk_pct_val: float) -> None:
    rpt    = result.get("report", {})
    trades = result.get("trades", [])
    params = result.get("parameters", {})

    # Multi-TF banner
    if result.get("analysis_mode") == "multi":
        parts = [f"Structure: **{result.get('structure_tf', '—')}**"]
        if result.get("trend_tf"):
            parts.insert(0, f"Trend: **{result['trend_tf']}**")
        if result.get("entry_tf"):
            parts.append(f"Entry: **{result['entry_tf']}**")
        st.info("Multi-TF — " + " | ".join(parts))

    # Goal badge
    status = rpt.get("goal_status", "INSUFFICIENT DATA")
    bc     = _STATUS_CLASS.get(status, "badge-insuf")
    st.markdown(
        f"**Goal Status:** &nbsp;<span class='badge {bc}'>{status}</span>",
        unsafe_allow_html=True,
    )
    st.write("")

    # 7 metrics
    c1,c2,c3,c4,c5,c6,c7 = st.columns(7)
    c1.metric("Trades",  rpt.get("total_trades", 0))
    c2.metric("Win Rate", f"{rpt.get('win_rate', 0):.1f}%")
    c3.metric("PF",       f"{rpt.get('profit_factor', 0):.2f}")
    c4.metric("Net R",    f"{rpt.get('net_r', 0):+.2f}R")
    _mr = rpt.get("monthly_return", 0)
    c5.metric("Monthly", f"{_mr:.2f}%",
              delta="On target" if GOAL_MR_MIN <= _mr <= GOAL_MR_MAX else "Off target",
              delta_color="normal" if GOAL_MR_MIN <= _mr <= GOAL_MR_MAX else "inverse")
    _dd = rpt.get("max_drawdown", 0)
    c6.metric("Max DD", f"{_dd:.2f}%",
              delta="OK" if _dd < GOAL_DD_LIM else "Exceeded",
              delta_color="normal" if _dd < GOAL_DD_LIM else "inverse")
    c7.metric("Open/Exp", rpt.get("open_trades", 0))
    st.write("")

    # Tabs within results
    teq, tmo, ttr, tgo = st.tabs([
        "📈 Equity Curve", "📅 Monthly", "📋 Trades", "🎯 Goals"
    ])

    with teq:
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
            title=f"Cumulative Return — {rpt.get('total_trades',0)} closed trades",
            xaxis_title="Trade #", yaxis_title="Return (%)",
            template="plotly_dark", height=380,
            margin=dict(l=10, r=10, t=45, b=10), showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        cd, ct = st.columns([1, 2])
        with cd:
            fig_p = go.Figure(go.Pie(
                labels=["Win","Loss","Open"],
                values=[rpt.get("win_trades",0), rpt.get("loss_trades",0), rpt.get("open_trades",0)],
                marker_colors=[pos_col, neg_col, "#ffa726"],
                hole=0.45, textinfo="label+percent",
            ))
            fig_p.update_layout(template="plotly_dark", height=250,
                                margin=dict(l=0,r=0,t=30,b=0), showlegend=False)
            st.plotly_chart(fig_p, use_container_width=True)
        with ct:
            st.dataframe(pd.DataFrame([
                ("Gross Profit R",  f"+{rpt.get('gross_profit_r',0):.2f}R"),
                ("Gross Loss R",    f"-{rpt.get('gross_loss_r',0):.2f}R"),
                ("Net R",           f"{rpt.get('net_r',0):+.2f}R"),
                ("Net Return",      f"{rpt.get('net_r',0)*risk_pct_val:.2f}%"),
                ("Avg Monthly",     f"{rpt.get('monthly_return',0):.2f}%"),
                ("Max Drawdown",    f"{rpt.get('max_drawdown',0):.2f}%"),
                ("Profit Factor",   f"{rpt.get('profit_factor',0):.2f}"),
                ("Win Rate",        f"{rpt.get('win_rate',0):.1f}%"),
            ], columns=["Metric","Value"]), use_container_width=True, hide_index=True)

    with tmo:
        monthly = rpt.get("monthly_breakdown", [])
        if not monthly:
            st.info("No closed trades to build monthly breakdown.")
        else:
            months  = [m["month"] for m in monthly]
            retpct  = [m["return_pct"] for m in monthly]
            fig_mo  = go.Figure()
            fig_mo.add_trace(go.Bar(
                x=months, y=retpct,
                marker_color=[pos_col if v >= 0 else neg_col for v in retpct],
                text=[f"{v:+.1f}%" for v in retpct], textposition="outside",
            ))
            fig_mo.add_hrect(y0=GOAL_MR_MIN, y1=GOAL_MR_MAX,
                            fillcolor="rgba(38,166,154,0.08)", line_width=0,
                            annotation_text="Target 3–5%", annotation_position="top right")
            fig_mo.add_hline(y=0, line_color="rgba(255,255,255,0.25)", line_dash="dash")
            fig_mo.update_layout(template="plotly_dark", height=350,
                                 margin=dict(l=10,r=10,t=45,b=10))
            st.plotly_chart(fig_mo, use_container_width=True)
            st.dataframe(pd.DataFrame(monthly).rename(columns={
                "month":"Month","trades":"Trades","wins":"Wins","losses":"Losses",
                "net_r":"Net R","return_pct":"Return %",
            }), use_container_width=True, hide_index=True)

    with ttr:
        if trades:
            df_tr = pd.DataFrame(trades)
            df_tr["Result"] = df_tr["result"].map(
                {"win":"✅ Win","loss":"❌ Loss","open":"⏳ Open"})
            col_map = {
                "date":"Date","time":"Time","direction":"Dir",
                "swept_level":"Level","entry":"Entry","sl":"SL","tp":"TP",
                "exit_price":"Exit","Result":"Result","r_multiple":"R","bars_held":"Bars",
            }
            disp = df_tr[[c for c in col_map if c in df_tr.columns]].rename(columns=col_map)
            st.dataframe(disp, use_container_width=True, hide_index=True)
        else:
            st.info("No signals detected. Try a smaller Swing Strength or longer data range.")

    with tgo:
        gd = rpt.get("goal_detail", {})
        _IC = {"PASS":"✅","WATCHLIST":"⚠️","FAIL":"❌"}
        _GC = {"PASS":"goal-pass","WATCHLIST":"goal-watch","FAIL":"goal-fail"}
        for label, key, hint in [
            ("Monthly Return", "monthly_return", "Avg monthly % gain over backtest period."),
            ("Max Drawdown",   "max_drawdown",   "Largest peak-to-trough equity decline."),
            ("Profit Factor",  "profit_factor",  "Gross profit ÷ gross loss (R)."),
        ]:
            g = gd.get(key, {})
            s = g.get("status","—")
            st.markdown(
                f'<div class="{_GC.get(s,"")}"><{_IC.get(s,"❓")} &nbsp;'
                f"<strong>{label}</strong><br>&nbsp;&nbsp;&nbsp;"
                f"Actual: <code>{g.get('value','N/A')}</code> &nbsp; "
                f"Target: <code>{g.get('target','—')}</code>"
                f"<br>&nbsp;&nbsp;&nbsp;<em>{hint}</em></div><br>",
                unsafe_allow_html=True,
            )

        st.divider()
        tfs_used = result.get("timeframes_used", [result.get("timeframe","—")])
        st.dataframe(pd.DataFrame([
            ("Symbol",        "XAUUSD"),
            ("Mode",          result.get("analysis_mode","—")),
            ("Timeframe(s)",  ", ".join(_sorted_tfs(tfs_used))),
            ("Module",        result.get("module","—").replace("_"," ").title()),
            ("Risk / Trade",  f"{params.get('risk_pct',risk_pct)}%"),
            ("RR Ratio",      f"{params.get('rr',rr)}R"),
            ("Swing N",       params.get("lookback",lookback)),
        ], columns=["Parameter","Value"]), use_container_width=True, hide_index=True)

    # Export paths
    exports = result.get("exports", {})
    if exports:
        with st.expander("📁 Exported files"):
            for k, v in exports.items():
                st.code(str(v), language=None)


# ═════════════════════════════════════════════════════════════════════════════
# TABS
# ═════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs([
    "▶ Run Analysis",
    "📂 Dataset Library",
    "🕒 Research History",
    "📤 Export Center",
])


# ═════════════════════════ TAB 1 — RUN ANALYSIS ═══════════════════════════════
with tab1:

    # ── Data source selector ──────────────────────────────────────────────────
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

    # Reset downstream state when source changes
    if st.session_state.get("_data_source") != data_src:
        for k in ("_tf_datasets","_upload_status","_upload_last_files","analysis"):
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
                for k in ("_tf_datasets","_upload_status","analysis"):
                    st.session_state.pop(k, None)
                st.session_state["_upload_last_files"] = current_key

            # Pre-upload TF assignment
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
                                    "Timeframe":  tf, "Filename": f.name,
                                    "Rows": "—", "Start": "—", "End": "—",
                                    "Status": f"⚠️ Skipped (duplicate {tf})",
                                })
                                continue

                            try:
                                resp = requests.post(
                                    f"{API}/api/v1/datasets",
                                    files={"file": (f.name, f.getvalue(), "text/csv")},
                                    params={"timeframe": tf},
                                    timeout=60,
                                )
                            except requests.exceptions.ConnectionError:
                                new_status.append({
                                    "Timeframe": tf, "Filename": f.name,
                                    "Rows": "—", "Start": "—", "End": "—",
                                    "Status": "❌ Connection error",
                                })
                                continue

                            if resp.status_code == 200:
                                info  = resp.json()
                                warns = len(info.get("validation", {}).get("warnings", []))
                                new_datasets[tf] = info["dataset_id"]
                                new_status.append({
                                    "Timeframe": tf,
                                    "Filename":  f.name,
                                    "Rows":      f"{info['rows']:,}",
                                    "Start":     info["start"][:10],
                                    "End":       info["end"][:10],
                                    "Status":    "✅ Stored" if warns == 0 else f"⚠️ {warns} warning(s)",
                                    "Dataset ID": info["dataset_id"][:8],
                                })

                            elif resp.status_code == 409:
                                # Duplicate — auto-reuse existing dataset
                                try:
                                    detail = resp.json().get("detail", {})
                                    eid    = detail.get("existing_dataset_id", "")
                                    etf    = detail.get("existing_timeframe", tf)
                                    new_datasets[tf] = eid
                                    new_status.append({
                                        "Timeframe": tf, "Filename": f.name,
                                        "Rows": "—", "Start": "—", "End": "—",
                                        "Status": f"♻️ Reusing existing dataset ({eid[:8]})",
                                        "Dataset ID": eid[:8],
                                    })
                                    st.warning(
                                        f"⚠️ **{f.name}** is already in the library — "
                                        f"reusing existing {etf} dataset."
                                    )
                                except Exception:
                                    new_status.append({
                                        "Timeframe": tf, "Filename": f.name,
                                        "Rows": "—", "Start": "—", "End": "—",
                                        "Status": "❌ Duplicate detected",
                                        "Dataset ID": "—",
                                    })

                            else:
                                try:
                                    detail = resp.json().get("detail", "upload error")
                                    if isinstance(detail, dict):
                                        detail = detail.get("message", str(detail))
                                except Exception:
                                    detail = "upload error"
                                new_status.append({
                                    "Timeframe": tf, "Filename": f.name,
                                    "Rows": "—", "Start": "—", "End": "—",
                                    "Status": f"❌ {detail}", "Dataset ID": "—",
                                })

                    st.session_state["_tf_datasets"]  = new_datasets
                    st.session_state["_upload_status"] = new_status
                    st.rerun()

        elif not uploaded_files:
            st.info("Drop one or more MT5 OHLCV CSV files above to begin.")

        # Post-upload status
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

        # Analysis mode
        mode_sel = st.radio(
            "Analysis Mode",
            ["Single Timeframe", "Multi-Timeframe"],
            horizontal=True,
            key="stored_mode",
        )

        ds_by_id = {d["dataset_id"]: d for d in all_datasets}
        labels   = {d["dataset_id"]: _dataset_label(d) for d in all_datasets}
        ids      = [d["dataset_id"] for d in all_datasets]

        new_datasets: dict[str, str] = {}

        if mode_sel == "Single Timeframe":
            chosen_id = st.selectbox(
                "Select dataset",
                ids,
                format_func=lambda x: labels.get(x, x),
            )
            tf_key = ds_by_id[chosen_id]["timeframe"]
            new_datasets = {tf_key: chosen_id}

        else:
            st.markdown(
                "Assign datasets to roles. **Structure TF** is required for Liquidity Sweep."
            )
            col_tr, col_st, col_en = st.columns(3)

            with col_tr:
                st.markdown("**Trend TF** *(optional)*")
                tr_sel = st.selectbox(
                    "Trend", ["None"] + ids,
                    format_func=lambda x: "None" if x == "None" else labels.get(x, x),
                    key="stored_trend",
                    label_visibility="collapsed",
                )

            with col_st:
                st.markdown("**Structure TF** *(required)*")
                st_sel = st.selectbox(
                    "Structure", ids,
                    format_func=lambda x: labels.get(x, x),
                    key="stored_structure",
                    label_visibility="collapsed",
                )

            with col_en:
                st.markdown("**Entry TF** *(optional)*")
                en_sel = st.selectbox(
                    "Entry", ["None"] + ids,
                    format_func=lambda x: "None" if x == "None" else labels.get(x, x),
                    key="stored_entry",
                    label_visibility="collapsed",
                )

            if st_sel:
                new_datasets[ds_by_id[st_sel]["timeframe"]] = st_sel
            if tr_sel and tr_sel != "None":
                new_datasets[ds_by_id[tr_sel]["timeframe"]] = tr_sel
            if en_sel and en_sel != "None":
                new_datasets[ds_by_id[en_sel]["timeframe"]] = en_sel

        if st.button("Use Selected Datasets", type="secondary"):
            st.session_state["_tf_datasets"] = new_datasets
            st.session_state.pop("analysis", None)
            st.rerun()

    # Show readiness summary
    tf_datasets = st.session_state.get("_tf_datasets", {})
    if tf_datasets:
        label_str = ", ".join(_sorted_tfs(list(tf_datasets.keys())))
        st.success(f"Datasets ready for analysis: **{label_str}**")

    # ── Step 2: Analysis Config + Run ─────────────────────────────────────────
    if not tf_datasets:
        st.stop()

    st.divider()
    st.subheader("Step 2 — Configure & Run")

    # Mode selector (upload flow)
    if data_src == "Upload New Files":
        analysis_mode = st.radio(
            "Analysis Mode",
            ["Single Timeframe", "Multi-Timeframe"],
            horizontal=True,
            key="upload_mode",
        )
    else:
        analysis_mode = mode_sel  # from stored datasets section

    # TF role selectors for multi-TF upload mode
    trend_tf = structure_tf = entry_tf = None
    display_tf = _sorted_tfs(list(tf_datasets.keys()))[0]
    req_dataset_id = None

    ok_tfs_sorted = _sorted_tfs(list(tf_datasets.keys()))

    if analysis_mode == "Single Timeframe":
        if len(ok_tfs_sorted) == 1:
            sel_tf = ok_tfs_sorted[0]
        else:
            sel_tf = st.selectbox("Analysis Timeframe", ok_tfs_sorted, key="single_tf_sel")
        req_dataset_id = tf_datasets[sel_tf]
        display_tf     = sel_tf

    elif analysis_mode == "Multi-Timeframe" and data_src == "Upload New Files":
        st.markdown(
            "Assign roles to your uploaded timeframes. **Structure TF** is required."
        )
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

    else:
        # Multi-TF stored: roles were set via role selectors above
        if data_src == "Use Stored Datasets":
            st_sel_id  = st.session_state.get("stored_structure")
            tr_sel_id  = st.session_state.get("stored_trend")
            en_sel_id  = st.session_state.get("stored_entry")
            if st_sel_id and st_sel_id in ds_by_id:
                structure_tf = ds_by_id[st_sel_id]["timeframe"]
                display_tf   = structure_tf
            if tr_sel_id and tr_sel_id != "None" and tr_sel_id in ds_by_id:
                trend_tf = ds_by_id[tr_sel_id]["timeframe"]
            if en_sel_id and en_sel_id != "None" and en_sel_id in ds_by_id:
                entry_tf = ds_by_id[en_sel_id]["timeframe"]

    # Research name
    research_name = st.text_input(
        "Research Name (optional)",
        placeholder="e.g. XAUUSD H1 Sweep — Jan 2024",
        key="research_name_input",
    )

    # Run button
    col_btn, col_desc = st.columns([1, 5])
    with col_btn:
        run_btn = st.button("▶  Run Analysis", type="primary", use_container_width=True)
    with col_desc:
        mode_label = "Single TF" if analysis_mode == "Single Timeframe" else "Multi-TF"
        st.caption(
            f"**Liquidity Sweep** | Mode: **{mode_label}** | "
            f"Risk: **{risk_pct}%** | RR: **{rr}R** | Swing N: **{lookback}**"
        )

    if run_btn:
        # Validate
        errors: list[str] = []
        if analysis_mode == "Multi-Timeframe":
            if not structure_tf:
                errors.append("Structure Timeframe is required for Multi-TF analysis.")
            elif structure_tf not in tf_datasets:
                errors.append(
                    f"Missing {structure_tf} data. "
                    f"Please upload XAUUSD_{structure_tf}_OHLCV.csv."
                )
            if trend_tf and trend_tf not in tf_datasets:
                errors.append(
                    f"Missing {trend_tf} data. "
                    f"Please upload XAUUSD_{trend_tf}_OHLCV.csv."
                )
            if entry_tf and entry_tf not in tf_datasets:
                errors.append(
                    f"Missing {entry_tf} data. "
                    f"Please upload XAUUSD_{entry_tf}_OHLCV.csv."
                )
        if errors:
            for e in errors:
                st.error(f"❌ {e}")
            st.stop()

        # Build payload
        if analysis_mode == "Single Timeframe":
            payload: dict = {
                "dataset_id":    req_dataset_id,
                "analysis_mode": "single",
                "module":        module,
                "timeframe":     display_tf,
                "risk_pct":      risk_pct,
                "rr":            rr,
                "lookback":      lookback,
                "research_name": research_name or None,
            }
        else:
            payload = {
                "dataset_ids":   tf_datasets,
                "analysis_mode": "multi",
                "trend_tf":      trend_tf,
                "structure_tf":  structure_tf,
                "entry_tf":      entry_tf,
                "module":        module,
                "timeframe":     display_tf,
                "risk_pct":      risk_pct,
                "rr":            rr,
                "lookback":      lookback,
                "research_name": research_name or None,
            }

        with st.spinner("Running backtest — detecting swings, executing trades…"):
            try:
                resp = requests.post(f"{API}/api/v1/analyze", json=payload, timeout=120)
            except requests.exceptions.ConnectionError:
                st.error("Lost connection to backend.")
                st.stop()

        if resp.status_code == 200:
            st.session_state["analysis"] = resp.json()
            st.success(
                f"Analysis complete — Research ID: **{resp.json().get('research_id','')[:8]}**"
            )
        else:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            st.error(f"Analysis failed ({resp.status_code}): {detail}")
            st.stop()

    # Results
    if "analysis" in st.session_state:
        st.divider()
        st.subheader("Step 3 — Results")
        _render_results(st.session_state["analysis"], risk_pct)


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
        # Main table
        tbl_rows = []
        for d in datasets:
            tbl_rows.append({
                "Dataset ID":  d["dataset_id"][:12] + "…",
                "Symbol":      d["symbol"],
                "TF":          d["timeframe"],
                "Filename":    d["filename"],
                "Rows":        f"{d['total_rows']:,}",
                "Start":       d["start_datetime"][:10],
                "End":         d["end_datetime"][:10],
                "Uploaded":    d["upload_datetime"][:16].replace("T", " "),
                "Status":      d["status"],
                "_id":         d["dataset_id"],
            })
        tbl_df = pd.DataFrame(tbl_rows)
        st.dataframe(
            tbl_df.drop(columns=["_id"]),
            use_container_width=True, hide_index=True,
        )
        st.caption(f"{len(datasets)} dataset(s) in library.")

        # Per-dataset actions
        st.divider()
        st.markdown("**Actions — select a dataset:**")
        sel_idx = st.selectbox(
            "Dataset",
            range(len(datasets)),
            format_func=lambda i: _dataset_label(datasets[i]),
            key="lib_sel_idx",
        )
        sel_d = datasets[sel_idx]
        sel_id = sel_d["dataset_id"]

        col_a, col_b, col_c = st.columns([1, 1, 3])

        with col_a:
            # Download dataset as CSV
            try:
                exp_resp = requests.get(
                    f"{API}/api/v1/datasets/{sel_id}/export", timeout=30
                )
                if exp_resp.status_code == 200:
                    st.download_button(
                        "⬇️  Export CSV",
                        data=exp_resp.content,
                        file_name=f"{sel_d['symbol']}_{sel_d['timeframe']}_{sel_id[:8]}.csv",
                        mime="text/csv",
                        key=f"dl_{sel_id}",
                    )
                else:
                    st.button("⬇️  Export CSV", disabled=True)
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

        # Delete confirmation
        if st.session_state.get("_confirm_delete_dataset") == sel_id:
            st.warning(
                f"Delete **{sel_d['filename']}** ({sel_d['timeframe']}) "
                f"and all its candles? This cannot be undone."
            )
            cc1, cc2 = st.columns(2)
            if cc1.button("✅ Yes, delete", type="primary", key="confirm_del"):
                dr = requests.delete(f"{API}/api/v1/datasets/{sel_id}", timeout=10)
                if dr.status_code == 200:
                    st.success("Dataset deleted.")
                    st.session_state.pop("_confirm_delete_dataset", None)
                    st.rerun()
                else:
                    st.error("Delete failed.")
            if cc2.button("✗ Cancel", key="cancel_del"):
                st.session_state.pop("_confirm_delete_dataset", None)


# ═════════════════════════ TAB 3 — RESEARCH HISTORY ══════════════════════════
with tab3:
    st.subheader("🕒 Research History")
    st.caption("All completed analysis runs, stored persistently in SQLite.")

    runs = _fetch_research_runs()

    if not runs:
        st.info("No research runs yet. Run an analysis in **Run Analysis** to create records.")
    else:
        # Summary table
        summary_rows = []
        for r in runs:
            tfs = json.loads(r.get("timeframes_used", "[]"))
            summary_rows.append({
                "Research ID":  r["research_id"][:12] + "…",
                "Name":         r.get("research_name", "—"),
                "Date":         r["created_datetime"][:16].replace("T", " "),
                "Module":       r["selected_module"].replace("_"," ").title(),
                "Mode":         r["timeframe_mode"],
                "TF(s)":        ", ".join(tfs),
                "Trades":       r["total_trades"],
                "Win%":         f"{r['win_rate']:.1f}",
                "PF":           f"{r['profit_factor']:.2f}",
                "Net R":        f"{r['net_r']:+.2f}",
                "Monthly%":     f"{r['monthly_return']:.2f}",
                "DD%":          f"{r['max_drawdown']:.2f}",
                "Status":       r["goal_status"],
                "_id":          r["research_id"],
            })

        sum_df = pd.DataFrame(summary_rows)
        st.dataframe(
            sum_df.drop(columns=["_id"]),
            use_container_width=True, hide_index=True,
        )
        st.caption(f"{len(runs)} research run(s) on record.")

        # Per-run actions
        st.divider()
        st.markdown("**Actions — select a research run:**")
        sel_run_idx = st.selectbox(
            "Research Run",
            range(len(runs)),
            format_func=lambda i: (
                f"{runs[i].get('research_name','—')} "
                f"[{runs[i]['created_datetime'][:10]}] "
                f"— {runs[i]['total_trades']} trades, {runs[i]['goal_status']}"
            ),
            key="hist_sel_idx",
        )
        sel_run = runs[sel_run_idx]
        sel_rid = sel_run["research_id"]

        # Export row
        col_tl, col_mo, col_su, col_rj, col_del = st.columns(5)

        for fmt, label, mime, col in [
            ("trade_log", "Trade Log CSV", "text/csv",             col_tl),
            ("monthly",   "Monthly CSV",   "text/csv",             col_mo),
            ("summary",   "Summary CSV",   "text/csv",             col_su),
            ("report",    "Report JSON",   "application/json",     col_rj),
        ]:
            with col:
                try:
                    er = requests.get(
                        f"{API}/api/v1/research/{sel_rid}/export/{fmt}", timeout=15
                    )
                    ext = "json" if fmt == "report" else "csv"
                    col.download_button(
                        f"⬇️ {label}",
                        data=er.content,
                        file_name=f"{fmt}_{sel_rid[:8]}.{ext}",
                        mime=mime,
                        key=f"dl_{fmt}_{sel_rid}",
                    )
                except Exception:
                    col.button(f"⬇️ {label}", disabled=True, key=f"btn_{fmt}_{sel_rid}")

        with col_del:
            if st.button("🗑️ Delete", key=f"del_run_{sel_rid}", type="secondary"):
                st.session_state["_confirm_delete_run"] = sel_rid

        # Delete confirmation
        if st.session_state.get("_confirm_delete_run") == sel_rid:
            st.warning("Delete this research run and all associated trade records?")
            dc1, dc2 = st.columns(2)
            if dc1.button("✅ Yes, delete", type="primary", key="confirm_del_run"):
                dr2 = requests.delete(f"{API}/api/v1/research/{sel_rid}", timeout=10)
                if dr2.status_code == 200:
                    st.success("Research run deleted.")
                    st.session_state.pop("_confirm_delete_run", None)
                    st.rerun()
                else:
                    st.error("Delete failed.")
            if dc2.button("✗ Cancel", key="cancel_del_run"):
                st.session_state.pop("_confirm_delete_run", None)

        # View / Re-run
        st.divider()
        view_col, rerun_col = st.columns(2)

        with view_col:
            with st.expander(f"🔍 View details — {sel_run.get('research_name','—')[:40]}"):
                # Reload full run including full_report
                try:
                    detail_resp = requests.get(
                        f"{API}/api/v1/research/{sel_rid}", timeout=10
                    )
                    if detail_resp.status_code == 200:
                        run_detail = detail_resp.json()
                        full_rpt = json.loads(run_detail.get("full_report") or "{}")
                        if full_rpt:
                            fake_result = {
                                "report":          full_rpt,
                                "trades":          [],
                                "analysis_mode":   run_detail.get("timeframe_mode", "single"),
                                "timeframe":       run_detail.get("timeframes_used","[]"),
                                "timeframes_used": json.loads(run_detail.get("timeframes_used","[]")),
                                "structure_tf":    None,
                                "trend_tf":        None,
                                "entry_tf":        None,
                                "module":          run_detail["selected_module"],
                                "parameters": {
                                    "risk_pct": run_detail["risk_percent"],
                                    "rr":       run_detail["reward_risk_ratio"],
                                    "lookback": run_detail["lookback"],
                                },
                                "exports": {},
                            }
                            _render_results(fake_result, run_detail["risk_percent"])
                        else:
                            st.info("Full report not available for this run.")
                except Exception as exc:
                    st.error(f"Could not load run detail: {exc}")

        with rerun_col:
            with st.expander("🔄 Re-run with same parameters"):
                try:
                    dr3 = requests.get(f"{API}/api/v1/research/{sel_rid}", timeout=10)
                    if dr3.status_code == 200:
                        rd = dr3.json()
                        did_map = json.loads(rd.get("dataset_ids_used", "{}"))
                        st.markdown(
                            f"**Module:** {rd['selected_module'].replace('_',' ').title()}  \n"
                            f"**Mode:** {rd['timeframe_mode']}  \n"
                            f"**Risk:** {rd['risk_percent']}%  |  "
                            f"**RR:** {rd['reward_risk_ratio']}R  |  "
                            f"**Swing N:** {rd['lookback']}"
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
                                # Build payload from stored parameters
                                tfs = json.loads(rd.get("timeframes_used","[]"))
                                rr_payload: dict = {
                                    "analysis_mode": rd["timeframe_mode"],
                                    "module":        rd["selected_module"],
                                    "timeframe":     tfs[0] if tfs else "H1",
                                    "risk_pct":      rd["risk_percent"],
                                    "rr":            rd["reward_risk_ratio"],
                                    "lookback":      rd["lookback"],
                                    "research_name": new_name,
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
                                        rr_resp = requests.post(
                                            f"{API}/api/v1/analyze",
                                            json=rr_payload, timeout=120,
                                        )
                                    except Exception:
                                        st.error("Connection error.")
                                        st.stop()

                                if rr_resp.status_code == 200:
                                    rerun_result = rr_resp.json()
                                    st.success(
                                        f"Re-run complete — "
                                        f"ID: {rerun_result.get('research_id','')[:8]}"
                                    )
                                    _render_results(rerun_result, rd["risk_percent"])
                                else:
                                    try:
                                        d = rr_resp.json().get("detail", rr_resp.text)
                                    except Exception:
                                        d = rr_resp.text
                                    st.error(f"Re-run failed: {d}")
                except Exception as exc:
                    st.error(f"Could not load run: {exc}")


# ═════════════════════════ TAB 4 — EXPORT CENTER ══════════════════════════════
with tab4:
    st.subheader("📤 Export Center")
    st.caption("Download exports for datasets and research runs.")

    # ── Dataset exports ────────────────────────────────────────────────────────
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
            er2 = requests.get(f"{API}/api/v1/datasets/{exp_did}/export", timeout=30)
            if er2.status_code == 200:
                fname = f"{exp_d['symbol']}_{exp_d['timeframe']}_{exp_did[:8]}.csv"
                st.download_button(
                    f"⬇️  Download {exp_d['symbol']} {exp_d['timeframe']} CSV "
                    f"({exp_d['total_rows']:,} rows)",
                    data=er2.content,
                    file_name=fname,
                    mime="text/csv",
                    type="primary",
                )
            else:
                st.error("Export failed.")
        except Exception:
            st.error("Could not connect to backend.")

    st.divider()

    # ── Research exports ───────────────────────────────────────────────────────
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
                f"[{exp_runs[i]['created_datetime'][:10]}] "
                f"— {exp_runs[i]['total_trades']} trades"
            ),
            key="exp_run_idx",
        )
        exp_run = exp_runs[exp_run_idx]
        exp_rid = exp_run["research_id"]

        st.markdown(
            f"**{exp_run.get('research_name','—')}** · "
            f"{exp_run['created_datetime'][:16].replace('T',' ')} · "
            f"{exp_run['total_trades']} trades · **{exp_run['goal_status']}**"
        )

        ec1, ec2, ec3, ec4 = st.columns(4)

        for fmt, label, mime, ext, col in [
            ("trade_log", "Trade Log",      "text/csv",          "csv",  ec1),
            ("monthly",   "Monthly Report", "text/csv",          "csv",  ec2),
            ("summary",   "Summary",        "text/csv",          "csv",  ec3),
            ("report",    "Full Report",    "application/json",  "json", ec4),
        ]:
            with col:
                try:
                    er3 = requests.get(
                        f"{API}/api/v1/research/{exp_rid}/export/{fmt}", timeout=15
                    )
                    col.download_button(
                        f"⬇️ {label}",
                        data=er3.content,
                        file_name=f"{fmt}_{exp_rid[:8]}.{ext}",
                        mime=mime,
                        key=f"exp_{fmt}_{exp_rid}",
                    )
                except Exception:
                    col.button(f"⬇️ {label}", disabled=True, key=f"exp_btn_{fmt}_{exp_rid}")

    st.divider()

    # ── Bulk export info ───────────────────────────────────────────────────────
    st.markdown("### Cumulative Log Files")
    st.caption(
        "These files accumulate across all analysis runs (appended each time).  \n"
        "Location: `data/exports/`"
    )
    st.code("data/exports/trade_log.csv        — all trades (every run)\n"
            "data/exports/research_summary.csv — all run summaries", language=None)
    st.markdown(
        "Individual per-run files are also saved after each analysis:  \n"
        "`trade_log_{id}.csv`, `monthly_{id}.csv`, `summary_{id}.csv`, `report_{id}.json`"
    )
