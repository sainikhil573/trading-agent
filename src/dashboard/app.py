"""
AI Trade Dashboard — Military Command Center v2
All 11 fixes: rgba Plotly colors, larger fonts, better metric cards,
trade card layout, VIX zones, donut, accuracy bar, spacing.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st

from src.analyzers.evaluation import (
    derive_data_quality,
    evaluate_accuracy_log as _eval_log,
    check_entry_trigger_met,
    audit_predictions_vs_triggers,
)
from src.fetchers.data_availability      import check_data_availability
from src.fetchers.intraday_loader        import list_intraday_files, save_uploaded_csv
from src.fetchers.broker_config          import get_provider_config
from src.analyzers.evaluation            import get_first_30min_range
from src.fetchers.intraday_csv_parser    import parse_candle_csv, compute_intraday_outcome

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    layout="wide",
    page_title="AI Trade Command",
    page_icon="🎯",
    initial_sidebar_state="auto",
)

# ---------------------------------------------------------------------------
# Constants & color maps
# ---------------------------------------------------------------------------

ROOT      = Path(__file__).parent.parent.parent
DATA_DIR  = ROOT / "data" / "processed"
IST       = pytz.timezone("Asia/Kolkata")
TODAY_STR = datetime.now(IST).strftime("%Y-%m-%d")
_JOURNAL_PATH = ROOT / "data" / "paper_trades" / "journal.json"

# Solid colors (never append alpha hex — breaks Plotly)
C = {
    "bg":      "#0a0f08",
    "card":    "#0d1a0b",
    "card2":   "#111e0e",
    "green":   "#8bc34a",
    "amber":   "#ffc107",
    "red":     "#ff5252",
    "success": "#4caf50",
    "neutral": "#546e7a",
    "blue":    "#42a5f5",
    "text":    "#e8f5e9",
    "text2":   "#90a4ae",
    "border":  "#1c3319",
    "dim":     "#e8f5e9",
}

# Plotly-safe rgba fill colors — no extra spaces, no hex+alpha (FIX 1)
RGBA_FILL = {
    "green":   "rgba(139, 195, 74, 0.20)",
    "amber":   "rgba(255, 193, 7, 0.25)",
    "blue":    "rgba(66, 165, 245, 0.20)",
    "red":     "rgba(255, 82, 82, 0.20)",
    "success": "rgba(76, 175, 80, 0.15)",
}

BIAS_COLOR   = {"BULLISH": C["success"], "BEARISH": C["red"],    "MIXED": C["amber"]}
VIX_COLOR    = {"LOW": C["success"], "NORMAL": C["green"],
                "ELEVATED": C["amber"], "HIGH": C["red"], "CRISIS": "#b71c1c"}
RISK_COLOR   = {"LOW": C["success"], "MEDIUM": C["amber"],
                "HIGH": C["red"], "VERY_HIGH": "#b71c1c"}
SIG_COLOR    = {"CALL": C["success"], "PUT": C["red"], "NEUTRAL": C["neutral"]}
ACTION_COLOR = {"GO": C["success"], "WAIT": C["amber"], "SKIP": C["red"]}
GRADE_COLOR  = {"A": C["success"], "B": C["green"], "C": C["amber"],
                "D": "#ff9800", "F": C["red"]}

# ---------------------------------------------------------------------------
# CSS — FIX 11: metric cards, spacing, font sizes
# ---------------------------------------------------------------------------

st.markdown(f"""
<style>
  .stApp {{ background-color: {C["bg"]} !important; }}
  .block-container {{ padding-top: 0.5rem !important; }}

  /* Body / general text */
  .stApp, .stApp p, .stApp div, .stApp span {{
    font-size: 14px !important;
  }}

  /* Metric cards */
  div[data-testid="stMetric"] {{
    background-color: #0f150a !important;
    border: 1px solid #1a2a15 !important;
    border-radius: 8px !important;
    padding: 16px !important;
    min-height: 90px !important;
  }}
  div[data-testid="stMetric"] label {{
    color: #c8e6c9 !important;
    font-size: 15px !important;
    font-family: monospace !important;
    letter-spacing: 1px !important;
    text-transform: uppercase !important;
  }}
  div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
    font-size: 48px !important;
    font-weight: 700 !important;
    color: {C["text"]} !important;
    font-family: monospace !important;
    line-height: 1.1 !important;
  }}
  div[data-testid="stMetric"] [data-testid="stMetricDelta"] {{
    font-size: 14px !important;
  }}

  /* Columns */
  div[data-testid="column"] {{ padding: 0 6px !important; }}

  /* FIX 9 — Expanders */
  div[data-testid="stExpander"] {{
    border: 1px solid #1a2a15 !important;
    border-radius: 4px !important;
  }}
  div[data-testid="stExpander"] summary {{
    color: {C["green"]} !important;
    font-family: monospace !important;
    font-size: 13px !important;
  }}

  /* Buttons */
  .stButton > button {{
    background: {C["card2"]} !important;
    color: {C["green"]} !important;
    border: 1px solid {C["green"]} !important;
    border-radius: 3px !important;
    font-family: monospace !important;
    letter-spacing: 1px !important;
    padding: 6px 16px !important;
    font-size: 13px !important;
  }}
  .stButton > button:hover {{ background: rgba(139,195,74,0.10) !important; }}

  hr {{ border-color: {C["border"]} !important; margin: 24px 0 !important; }}

  /* Alert text size */
  .stAlert {{ border-radius: 4px !important; font-size: 15px !important; }}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _section_header(title: str) -> str:
    return (
        f'<div style="border-left:3px solid {C["green"]};padding-left:12px;'
        f'margin:24px 0 10px 0;font-size:18px;letter-spacing:2px;'
        f'color:{C["green"]};font-family:monospace;font-weight:700">{title}</div>'
    )

def _badge(text: str, bg: str = "#333", size: int = 13) -> str:
    return (
        f'<span style="background:{bg};color:#fff;padding:4px 13px;'
        f'border-radius:3px;font-weight:700;font-size:{size}px;'
        f'margin-right:6px;letter-spacing:0.5px;white-space:nowrap;'
        f'font-family:monospace">{text}</span>'
    )

def _pill(text: str, color: str = "#555", size: int = 13) -> str:
    return (
        f'<span style="background:{color}22;color:{color};'
        f'border:1px solid {color}55;padding:3px 11px;border-radius:3px;'
        f'font-size:{size}px;margin:2px 3px;display:inline-block;font-family:monospace">'
        f'{text}</span>'
    )

def _conf_bar(label: str, value: float, max_val: int = 10) -> str:
    """FIX 4 — 12px bar height, 13px labels."""
    pct   = min(value / max_val * 100, 100)
    color = C["red"] if value < 5 else C["amber"] if value < 7 else C["green"]
    short = {
        "global_cues":         "GLOBAL CUES",
        "institutional_flow":  "INSTITUTIONAL",
        "derivative_signals":  "DERIVATIVES",
        "technical_analysis":  "TECHNICALS",
        "volume_confirmation": "VOLUME",
        "news_sentiment":      "NEWS",
        "event_risk":          "EVENT RISK",
        "risk_reward_quality": "RISK:REWARD",
    }.get(label, label.upper())
    return (
        f'<div style="margin:6px 0">'
        f'<div style="display:flex;justify-content:space-between;'
        f'font-size:14px;color:{C["text2"]};font-family:monospace">'
        f'<span>{short}</span>'
        f'<span style="color:{color};font-weight:700">{value}/10</span></div>'
        f'<div style="background:#1a2e1a;border-radius:2px;height:12px;margin-top:3px">'
        f'<div style="background:{color};width:{pct}%;height:12px;border-radius:2px">'
        f'</div></div></div>'
    )

def _cue_card(label: str, last: float, pct: float, invert: bool = False) -> str:
    """FIX 8 — 80px min-height, 20px value, 18px % change, green border."""
    arrow = "▲" if pct >= 0 else "▼"
    color = C["green"] if pct >= 0 else C["red"]
    if invert:
        color = C["red"] if pct >= 0 else C["success"]
    return (
        f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
        f'padding:12px 8px;border-radius:6px;text-align:center;min-height:80px">'
        f'<div style="font-size:13px;color:{C["neutral"]};font-family:monospace;'
        f'letter-spacing:1px;margin-bottom:4px">{label.upper()}</div>'
        f'<div style="font-size:20px;font-weight:700;color:#ffffff;'
        f'font-family:monospace">{last:,.2f}</div>'
        f'<div style="font-size:16px;color:{color};font-weight:600;font-family:monospace">'
        f'{arrow} {pct:+.2f}%</div></div>'
    )

def _price_box(label: str, val, color: str) -> str:
    return (
        f'<div style="background:#0f0f0f;border:1px solid #2a3a2a;'
        f'border-radius:4px;padding:10px 8px;text-align:center;min-height:70px">'
        f'<div style="font-size:14px;color:#c8e6c9;letter-spacing:1px;'
        f'font-family:monospace;margin-bottom:4px">{label}</div>'
        f'<div style="font-size:24px;font-weight:700;color:{color};'
        f'font-family:monospace">{val}</div></div>'
    )

def _level_chip(val, kind: str) -> str:
    color = C["success"] if kind == "support" else C["red"]
    return _pill(f"{val:,.0f}", color, size=15)

# ---------------------------------------------------------------------------
# Plotly chart builders
# ---------------------------------------------------------------------------

def _base_layout(title: str, height: int = 250) -> dict:
    return dict(
        paper_bgcolor=C["card"],
        plot_bgcolor=C["bg"],
        font=dict(color=C["text"], family="monospace"),
        margin=dict(l=10, r=70, t=46, b=10),
        title=dict(text=title, font=dict(size=16, color=C["green"]), x=0.5),
        height=height,
    )


def make_bias_donut(confidence_breakdown: dict) -> go.Figure:
    """FIX 6 — valid plotly colors, height 300px, center text 7.2/10."""
    label_map = {
        "global_cues":         "GLOBAL",
        "institutional_flow":  "INST",
        "derivative_signals":  "DERIVS",
        "technical_analysis":  "TECH",
        "volume_confirmation": "VOL",
        "news_sentiment":      "NEWS",
        "event_risk":          "EVENTS",
        "risk_reward_quality": "R:R",
    }
    labels = [label_map.get(k, k.upper()) for k in confidence_breakdown]
    values = list(confidence_breakdown.values())
    # Fixed palette cycling — valid plotly hex, no alpha (FIX 6)
    _DONUT_PALETTE = ['#4caf50', '#ffc107', '#ff5252', '#c8e6c9']
    colors = [_DONUT_PALETTE[i % len(_DONUT_PALETTE)] for i in range(len(values))]
    avg    = sum(values) / len(values) if values else 0

    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.62,
        marker=dict(colors=colors, line=dict(color=C["bg"], width=1)),
        textinfo="label+value",
        textfont=dict(size=11, color=C["text"]),
        hovertemplate="%{label}: %{value}/10<extra></extra>",
    ))
    layout = _base_layout("CONFIDENCE INTEL", height=300)
    layout["showlegend"] = False
    layout["annotations"] = [dict(
        text=f"<b>{avg:.1f}</b><br>/10",
        x=0.5, y=0.5,
        font=dict(size=22, color=C["green"]),
        showarrow=False,
    )]
    fig.update_layout(**layout)
    return fig


@st.cache_data(ttl=3600)
def fetch_vix_trend() -> go.Figure | None:
    """FIX 5 — green line, zone fills (rgba), current VIX annotation, y 8-35, height 250."""
    import yfinance as yf
    try:
        hist = yf.Ticker("^INDIAVIX").history(period="14d", interval="1d")
        if hist.empty:
            return None
        dates  = [d.strftime("%d %b") for d in hist.index]
        values = hist["Close"].round(2).tolist()
        current = values[-1]

        fig = go.Figure()

        # Zone fills — rgba for plotly fillcolor (FIX 1)
        zones = [
            (8,  13, "rgba(76, 175, 80, 0.10)",  C["success"], "LOW"),
            (13, 17, "rgba(255, 193, 7, 0.10)",  C["amber"],   "NORMAL"),
            (17, 22, "rgba(255, 152, 0, 0.10)",  "#ff9800",    "ELEVATED"),
            (22, 35, "rgba(244, 67, 54, 0.10)",  C["red"],     "HIGH"),
        ]
        for y0, y1, fill, col, lbl in zones:
            fig.add_hrect(y0=y0, y1=y1, fillcolor=fill, line_width=0)
            fig.add_annotation(
                x=1.02, y=(y0 + y1) / 2,
                xref="paper", yref="y",
                text=lbl, showarrow=False,
                font=dict(size=11, color=col),
                xanchor="left",
            )

        # VIX line — military green (FIX 5)
        fig.add_trace(go.Scatter(
            x=dates, y=values,
            mode="lines+markers",
            line=dict(color=C["green"], width=2.5),
            marker=dict(size=6, color=C["green"]),
            fill="tozeroy",
            fillcolor="rgba(139, 195, 74, 0.08)",   # rgba, not hex-alpha (FIX 1)
            hovertemplate="%{x}: VIX %{y:.2f}<extra></extra>",
        ))

        # Current VIX annotation (FIX 5)
        vix_col = C["success"] if current < 13 else C["amber"] if current < 17 else C["red"]
        fig.add_annotation(
            x=dates[-1], y=current,
            text=f" VIX: {current:.1f}",
            showarrow=True, arrowhead=2,
            arrowcolor=vix_col, arrowwidth=1.5,
            font=dict(size=13, color=vix_col),
            bgcolor=C["bg"],
            bordercolor=vix_col, borderwidth=1,
            ax=45, ay=-32,
        )

        layout = _base_layout("INDIA VIX — 14 DAY TREND", height=250)
        layout["xaxis"] = dict(gridcolor="#1a2e1a", tickfont=dict(size=12))
        layout["yaxis"] = dict(range=[8, 35], gridcolor="#1a2e1a", tickfont=dict(size=12))
        layout["showlegend"] = False
        fig.update_layout(**layout)
        return fig
    except Exception:
        return None


def make_radar_chart(trades: list[dict]) -> go.Figure:
    """FIX 1 — rgba fillcolor (not hex+alpha) for all traces."""
    cats = ["GLOBAL", "INSTITUTIONAL", "DERIVATIVES",
            "TECHNICALS", "VOLUME", "NEWS", "EVENTS", "R:R"]
    keys = ["global_cues", "institutional_flow", "derivative_signals",
            "technical_analysis", "volume_confirmation", "news_sentiment",
            "event_risk", "risk_reward_quality"]

    # rgba fills — Plotly does NOT accept '#8bc34a22' style (FIX 1)
    fill_colors = [
        RGBA_FILL["green"],
        RGBA_FILL["amber"],
        RGBA_FILL["blue"],
    ]
    line_colors = [C["green"], C["amber"], C["blue"]]

    fig = go.Figure()
    for i, t in enumerate(trades[:3]):
        bd   = t.get("confidence_breakdown", {})
        vals = [bd.get(k, 0) for k in keys]
        col  = line_colors[i % len(line_colors)]
        fig.add_trace(go.Scatterpolar(
            r=vals + [vals[0]],
            theta=cats + [cats[0]],
            fill="toself",
            fillcolor=fill_colors[i % len(fill_colors)],   # rgba only
            line=dict(color=col, width=2),
            name=f"{t.get('symbol')} {t.get('signal')}",
            hovertemplate="%{theta}: %{r}/10<extra></extra>",
        ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True, range=[0, 10],
                gridcolor="#1a2e1a", color=C["text2"],
                tickfont=dict(size=10),
            ),
            angularaxis=dict(
                gridcolor="#1a2e1a", color=C["text2"],
                tickfont=dict(size=11),
            ),
            bgcolor=C["bg"],
        ),
        paper_bgcolor=C["card"],
        font=dict(color=C["text"], family="monospace"),
        margin=dict(l=20, r=20, t=46, b=30),
        title=dict(text="INTELLIGENCE RADAR", font=dict(size=16, color=C["green"]), x=0.5),
        legend=dict(
            font=dict(size=11), bgcolor=C["card"],
            x=0.5, xanchor="center", y=-0.14, orientation="h",
        ),
        showlegend=len(trades) > 1,
        height=310,
    )
    return fig


def make_accuracy_bar(valid_log: list[dict]) -> go.Figure:
    """FIX 7 — per-day accuracy % bars, green ≥50% / red <50%, data labels, height 220px."""
    df = pd.DataFrame(valid_log)
    daily = df.groupby("date").agg(
        correct=("was_correct", "sum"),
        total=("was_correct", "count"),
    ).tail(7)
    daily["pct"] = (daily["correct"] / daily["total"] * 100).round(0)
    bar_colors  = ['#4caf50' if p >= 50 else '#ff5252' for p in daily["pct"]]
    text_labels = [f"{p:.0f}%" for p in daily["pct"]]

    fig = go.Figure(go.Bar(
        x=daily.index,
        y=daily["pct"],
        marker_color=bar_colors,
        text=text_labels,
        textposition="outside",
        textfont=dict(size=12, color=C["text"]),
        hovertemplate="%{x}<br>Accuracy: %{y:.0f}%<extra></extra>",
    ))
    layout = _base_layout("7-DAY DIRECTION ACCURACY", height=220)
    layout["xaxis"]      = dict(gridcolor="#1a2e1a", showgrid=False, tickfont=dict(size=13))
    layout["yaxis"]      = dict(range=[0, 115], gridcolor="#1a2e1a",
                                tickformat=".0f", ticksuffix="%", tickfont=dict(size=13))
    layout["showlegend"] = False
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# SIDEBAR — Intraday candle CSV upload for outcome verification (TASK 3)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown(
        f'<div style="font-family:monospace;font-size:13px;font-weight:700;'
        f'color:#8bc34a;letter-spacing:1px;margin-bottom:8px">INTRADAY OUTCOME CHECK</div>',
        unsafe_allow_html=True,
    )
    st.caption("Upload Zerodha Kite or TradingView 5m/15m CSV to verify today's paper trades.")

    _sb_file = st.file_uploader(
        "Candle CSV (Zerodha / TradingView)",
        type=["csv"],
        key="sidebar_candle_upload",
        help="Columns accepted: Zerodha (date, time, open, high, low, close, volume) "
             "or TradingView (time, open, high, low, close, volume)",
    )

    if _sb_file is not None:
        _sb_result = parse_candle_csv(_sb_file.getvalue())
        if _sb_result["errors"]:
            for _e in _sb_result["errors"]:
                st.error(f"CSV error: {_e}")
        else:
            _sb_df = _sb_result["df"]
            st.success(
                f"Parsed {len(_sb_df)} bars — format: {_sb_result['format_detected']}"
            )
            if _sb_result["warnings"]:
                for _w in _sb_result["warnings"]:
                    st.warning(_w)

            # Load journal and compute outcomes for each open trade
            if _JOURNAL_PATH.exists():
                _sb_journal = json.loads(_JOURNAL_PATH.read_text(encoding="utf-8"))
                _sb_trades  = [
                    t for t in _sb_journal.get("trades", [])
                    if t.get("date") == TODAY_STR
                ]
                if _sb_trades:
                    st.markdown(
                        f'<div style="font-family:monospace;font-size:11px;'
                        f'color:#90a4ae;margin:8px 0 4px 0">TODAY\'S PAPER TRADES</div>',
                        unsafe_allow_html=True,
                    )
                    for _sbt in _sb_trades:
                        _oc = compute_intraday_outcome(_sb_df, _sbt)
                        _oc_color = "#4caf50" if _oc.get("outcome") in (
                            "TARGET_1_HIT","TARGET_2_HIT"
                        ) else ("#ff5252" if _oc.get("outcome") == "SL_HIT" else "#ffc107")
                        st.markdown(
                            f'**{_sbt["symbol"]} {_sbt["signal"]}**  \n'
                            f'Entry trigger: {"✓" if _oc.get("entry_triggered") else "✗"}  '
                            f'SL hit: {"✓" if _oc.get("sl_hit") else "✗"}  '
                            f'T1: {"✓" if _oc.get("t1_hit") else "✗"}  '
                            f'T2: {"✓" if _oc.get("t2_hit") else "✗"}  \n'
                            f'Close: {_oc.get("final_close","—")}  '
                            f'**Outcome: {_oc.get("outcome","UNKNOWN")}**'
                        )
                else:
                    st.info("No paper trades found for today.")
            else:
                st.info("No journal file found.")

    st.markdown("---")
    st.caption("Paper trades only — no live execution")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

morning_path    = DATA_DIR / f"trade_brief_morning_{TODAY_STR}.json"
preopen_path    = DATA_DIR / f"trade_brief_preopen_{TODAY_STR}.json"
accuracy_path   = DATA_DIR / "prediction_accuracy.json"
postmarket_path = DATA_DIR / f"post_market_{TODAY_STR}.json"

if not morning_path.exists():
    st.error(f"No morning brief for {TODAY_STR}. Run `python main.py` to generate analysis.")
    st.info("Checking again in 30s...")
    time.sleep(30)
    st.rerun()

brief   = json.loads(morning_path.read_text(encoding="utf-8"))
preopen = json.loads(preopen_path.read_text(encoding="utf-8")) if preopen_path.exists() else None
acc_log = json.loads(accuracy_path.read_text(encoding="utf-8")) if accuracy_path.exists() else []
pm_data = json.loads(postmarket_path.read_text(encoding="utf-8")) if postmarket_path.exists() else None

ctx      = brief.get("market_context", {})
meta     = brief.get("_meta", {})
cues     = meta.get("global_cues", {})
valid_log = [r for r in acc_log if not r.get("error")]

# ---------------------------------------------------------------------------
# Smart refresh — file mtime + JS 300s countdown (FIX 1 of previous set)
# ---------------------------------------------------------------------------

if "last_mtime" not in st.session_state:
    st.session_state.last_mtime = morning_path.stat().st_mtime

try:
    cur_mtime = morning_path.stat().st_mtime
    if cur_mtime != st.session_state.last_mtime:
        st.session_state.last_mtime = cur_mtime
        st.rerun()
except Exception:
    pass

# ---------------------------------------------------------------------------
# HEADER — FIX 2: 28px bold title
# ---------------------------------------------------------------------------

bias  = ctx.get("overall_market_bias", "MIXED")
vzone = ctx.get("vix_zone", "NORMAL")
risk  = ctx.get("overall_risk_rating", "MEDIUM")
# trading_recommended is updated post-gate — use the value from the (possibly updated) context
t_ok  = brief.get("market_context", {}).get("trading_recommended", True)
analysis_time = brief.get("analysis_time", "08:00 IST")
date_raw = brief.get("date", TODAY_STR)
try:
    display_date = datetime.strptime(date_raw, "%Y-%m-%d").strftime("%d %b %Y — %A").upper()
except Exception:
    display_date = date_raw.upper()

now_str = datetime.now(IST).strftime("%H:%M:%S IST")

st.markdown(f"""
<div style="background:{C["card2"]};border-bottom:2px solid {C["border"]};
padding:10px 18px;margin-bottom:10px;display:flex;justify-content:space-between;
align-items:center;flex-wrap:wrap;gap:8px">
  <div>
    <span style="font-family:monospace;font-size:28px;font-weight:bold;
    color:{C["green"]};letter-spacing:2px">▶ AI TRADE COMMAND</span>
    <span style="font-family:monospace;font-size:12px;color:{C["neutral"]};
    margin-left:14px">// {display_date}</span>
  </div>
  <div style="display:flex;align-items:center;gap:14px">
    <span style="font-family:monospace;font-size:12px;color:{C["neutral"]}">
      BRIEF: {analysis_time} | NOW: {now_str}
    </span>
    <span id="cc-timer" style="font-family:monospace;font-size:12px;color:{C["amber"]}"></span>
  </div>
</div>
<script>
(function(){{
  var cd=300;
  function tick(){{
    var el=document.getElementById('cc-timer');
    if(el) el.textContent='[SYNC IN '+cd+'s]';
    cd--;
    if(cd<0){{window.location.reload();}}else{{setTimeout(tick,1000);}}
  }}
  tick();
}})();
</script>
""", unsafe_allow_html=True)

# Status row + sync button
hc1, hc2 = st.columns([9, 1])
with hc1:
    deploy_lbl = "DEPLOY" if t_ok else "STAND DOWN"
    st.markdown(
        _badge(bias, BIAS_COLOR.get(bias, C["neutral"]), 13) +
        _badge(f"VIX: {vzone}", VIX_COLOR.get(vzone, C["neutral"]), 13) +
        _badge(f"RISK: {risk}", RISK_COLOR.get(risk, C["neutral"]), 13) +
        _badge(deploy_lbl, C["success"] if t_ok else C["red"], 13) +
        (f' <span style="font-size:14px;color:{C["amber"]};font-family:monospace">'
         f'⚠ {ctx["trading_caution"]}</span>' if ctx.get("trading_caution") else ""),
        unsafe_allow_html=True,
    )
with hc2:
    if st.button("↻ SYNC"):
        st.rerun()

# FIX 2 — alert text 14px
for w in brief.get("event_warnings", []):
    st.markdown(
        f'<div style="background:#1a0f00;border-left:3px solid {C["amber"]};'
        f'padding:12px 16px;font-family:monospace;font-size:15px;line-height:1.8;'
        f'color:#ffffff;margin:4px 0">'
        f'[ALERT] {w}</div>',
        unsafe_allow_html=True,
    )
if any(t.get("expiry_day_warning") for t in brief.get("trades", [])):
    st.markdown(
        f'<div style="background:#1a0000;border-left:3px solid {C["red"]};'
        f'padding:12px 16px;font-family:monospace;font-size:15px;line-height:1.8;'
        f'color:#ffffff;margin:4px 0">'
        f'[EXPIRY] OPTIONS EXPIRE TODAY — EXIT / ROLL ALL POSITIONS BEFORE 13:00 IST</div>',
        unsafe_allow_html=True,
    )

# Post-gate recommendation status banner (shown when gates have been applied)
_gs              = brief.get("_gate_summary", {})
_final_status    = _gs.get("final_recommendation_status", "")
_post_gate_note  = brief.get("post_gate_summary", "")
_total_actionable = _gs.get("total_actionable", len(brief.get("trades", [])))

if brief.get("_gates_applied") and _final_status:
    if _final_status == "ACTIONABLE_TRADES_AVAILABLE":
        _pg_bg, _pg_border, _pg_label = "#001500", C["success"], "[GATES PASSED]"
    elif _final_status == "DATA_INSUFFICIENT":
        _pg_bg, _pg_border, _pg_label = "#1a0800", C["red"], "[DATA INSUFFICIENT]"
    else:
        _pg_bg, _pg_border, _pg_label = "#1a0800", C["amber"], "[NO ACTIONABLE TRADE]"
    st.markdown(
        f'<div style="background:{_pg_bg};border-left:4px solid {_pg_border};'
        f'padding:12px 16px;font-family:monospace;font-size:14px;line-height:1.8;'
        f'color:#ffffff;margin:6px 0">'
        f'<span style="color:{_pg_border};font-weight:700">{_pg_label} </span>'
        f'{_post_gate_note}</div>',
        unsafe_allow_html=True,
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# MARKET SNAPSHOT — FIX 3: metric card size via CSS above
# ---------------------------------------------------------------------------

st.markdown(_section_header("MARKET SNAPSHOT"), unsafe_allow_html=True)

vix_val   = meta.get("vix_at_8am", "—")
n_spot    = meta.get("nifty_spot",  "—")
n_pcr     = meta.get("nifty_pcr",   "—")
bn_spot   = meta.get("banknifty_spot", "—")
g_score   = ctx.get("global_score", "—")
crude_d   = cues.get("Crude Oil WTI", {})
crude_v   = crude_d.get("last")
crude_pct = crude_d.get("pct_change", 0) or 0
fii       = meta.get("fii_dii", {})
fii_net   = fii.get("fii_net_buy")

def _fmt(v, fmt="{:.2f}"):
    return fmt.format(v) if isinstance(v, (int, float)) else str(v)

mc = st.columns(7)
mc[0].metric("INDIA VIX",    _fmt(vix_val))
mc[1].metric("NIFTY",        f"{n_spot:,.1f}"  if isinstance(n_spot,  (int,float)) else str(n_spot))
mc[2].metric("NIFTY PCR",    _fmt(n_pcr))
mc[3].metric("BANKNIFTY",    f"{bn_spot:,.1f}" if isinstance(bn_spot, (int,float)) else str(bn_spot))
mc[4].metric("CRUDE WTI",
             f"${crude_v:.2f}" if isinstance(crude_v, (int,float)) else "—",
             delta=f"{crude_pct:+.2f}%" if crude_pct else None,
             delta_color="inverse")
mc[5].metric("GLOBAL SCORE", f"{g_score:+.0f} / +3" if isinstance(g_score, (int,float)) else str(g_score))
_fii_dii_raw    = meta.get("fii_dii", {})
_fii_unavailable = (
    _fii_dii_raw.get("fii_net_buy") == 0.0
    and _fii_dii_raw.get("dii_net_buy") == 0.0
)
_fii_prev_day    = _fii_dii_raw.get("is_prev_day", False)
_fii_fallback_dt = _fii_dii_raw.get("fallback_date", "")
mc[6].metric("FII NET (Cr)",
             "Unavailable" if _fii_unavailable else (
                 f"₹{fii_net:+,.0f}" if isinstance(fii_net, (int,float)) else "—"
             ),
             delta=(
                 "pre-market zero" if _fii_unavailable else
                 (f"prev-day: {_fii_fallback_dt}" if _fii_prev_day else None)
             ),
             delta_color="off")

st.markdown("---")

# ---------------------------------------------------------------------------
# DATA AVAILABILITY — comprehensive layer status + quality warnings
# ---------------------------------------------------------------------------

_health_dir  = ROOT / "data" / "health"
_health_path = _health_dir / f"health_{TODAY_STR}.json"
_health      = json.loads(_health_path.read_text(encoding="utf-8")) if _health_path.exists() else None

_dq       = derive_data_quality(meta, ctx)
_da       = check_data_availability(meta)
_da_layers = _da["layers"]

st.markdown(_section_header("DATA AVAILABILITY"), unsafe_allow_html=True)

# Availability status table
_ok_layers   = [l for l in _da_layers if l["available"]]
_miss_layers = [l for l in _da_layers if not l["available"]]

_da_c1, _da_c2 = st.columns(2)
with _da_c1:
    _ok_html = "".join(
        f'<div style="display:flex;justify-content:space-between;font-size:12px;'
        f'font-family:monospace;padding:3px 0;color:{C["success"]}">'
        f'<span>✓ {l["name"]}</span>'
        f'<span style="color:{C["neutral"]};font-size:11px">{l["weight"].split("(")[0].strip()}</span>'
        f'</div>'
        for l in _ok_layers
    ) or f'<div style="color:{C["red"]};font-family:monospace;font-size:12px">None</div>'
    st.markdown(
        f'<div style="font-size:11px;color:{C["success"]};font-family:monospace;'
        f'letter-spacing:1px;margin-bottom:4px">AVAILABLE ({len(_ok_layers)})</div>'
        f'<div style="background:#0a100a;border:1px solid {C["border"]};'
        f'padding:8px 10px;border-radius:3px">{_ok_html}</div>',
        unsafe_allow_html=True,
    )

with _da_c2:
    _miss_html = "".join(
        f'<div style="font-size:12px;font-family:monospace;padding:3px 0">'
        f'<span style="color:{"#ff9800" if l["required"] else C["neutral"]}">{"✗" if l["required"] else "—"} {l["name"]}</span>'
        f'<div style="font-size:11px;color:{C["text2"]};line-height:1.4;margin:1px 0 4px 12px">'
        f'{l["what_breaks"][:90]}{"..." if len(l["what_breaks"]) > 90 else ""}</div>'
        f'</div>'
        for l in _miss_layers
    ) or f'<div style="color:{C["success"]};font-family:monospace;font-size:12px">All layers available</div>'
    _miss_label_color = C["red"] if _da["missing_required"] else C["neutral"]
    st.markdown(
        f'<div style="font-size:11px;color:{_miss_label_color};font-family:monospace;'
        f'letter-spacing:1px;margin-bottom:4px">MISSING / UNAVAILABLE ({len(_miss_layers)})</div>'
        f'<div style="background:#0a100a;border:1px solid {C["border"]};'
        f'padding:8px 10px;border-radius:3px">{_miss_html}</div>',
        unsafe_allow_html=True,
    )

# Health source status — compact pill row inside DATA AVAILABILITY
if _health:
    _hsrc  = _health.get("sources", {})
    _hovrl = _health.get("overall", "UNKNOWN")
    _h_color = {
        "HEALTHY":  C["success"],
        "CACHED":   C["amber"],
        "DEGRADED": C["amber"],
        "CRITICAL": C["red"],
    }.get(_hovrl, C["neutral"])
    _h_pills = "".join(
        _pill(
            f"{k.upper().replace('_', ' ')}: {v['status']}",
            C["success"] if v["status"] == "FRESH" else (
                C["amber"] if v["status"] == "CACHED" else C["red"]
            ),
            11,
        )
        for k, v in _hsrc.items()
    )
    st.markdown(
        f'<div style="margin:8px 0 4px 0;font-size:11px;color:{C["text2"]};'
        f'font-family:monospace;letter-spacing:1px">DATA HEALTH — {_health.get("time","?")} '
        f'<span style="color:{_h_color};font-weight:700">[{_hovrl}]</span></div>'
        f'<div style="margin-bottom:6px">{_h_pills}</div>',
        unsafe_allow_html=True,
    )

# Actionability banner
if _da["actionable"]:
    _action_banner_bg    = "#001500"
    _action_banner_color = C["success"]
    _action_banner_text  = "ANALYSIS ACTIONABLE — All required data layers present"
else:
    _action_banner_bg    = "#1a0800"
    _action_banner_color = C["amber"]
    _action_banner_text  = (
        f"ANALYSIS DEGRADED — {len(_da['missing_required'])} required layer(s) missing: "
        + ", ".join(_da["missing_required"])
    )
st.markdown(
    f'<div style="background:{_action_banner_bg};border-left:3px solid {_action_banner_color};'
    f'padding:10px 14px;font-family:monospace;font-size:13px;color:#ffffff;margin:8px 0">'
    f'<span style="color:{_action_banner_color};font-weight:700">[STATUS] </span>'
    f'{_action_banner_text}</div>',
    unsafe_allow_html=True,
)

# Intraday data banner + provider status
_prov_cfg  = _da.get("provider_config") or get_provider_config()
_prov_name = _prov_cfg.get("provider", "csv").upper()
_prov_miss = _prov_cfg.get("missing_creds", [])
_prov_fall = _prov_cfg.get("fallback_active", False)

_id_color = C["success"] if _da["intraday_available"] else C["amber"]
_id_text  = (
    f'Intraday candles available for: {", ".join(_da["intraday_symbols"])} '
    f'→ outcome resolution will use fill sequence (not OHLC extremes)'
    if _da["intraday_available"] else
    "Intraday data missing: cannot confirm whether SL or target hit first. "
    "Ambiguous outcomes marked OUTCOME_UNKNOWN — not counted in win rate."
)
st.markdown(
    f'<div style="background:#{"001020" if _da["intraday_available"] else "120800"};'
    f'border-left:3px solid {_id_color};'
    f'padding:10px 14px;font-family:monospace;font-size:13px;color:#ffffff;margin:4px 0">'
    f'<span style="color:{_id_color};font-weight:700">[INTRADAY] </span>{_id_text}</div>',
    unsafe_allow_html=True,
)

# Provider status line
_provider_label_color = C["success"] if not _prov_fall else C["amber"]
_provider_status = (
    f"Active provider: {_da['intraday_provider']}"
    if not _prov_fall else
    f"Provider: {_prov_name} requested but falling back to CSV "
    f"(missing credentials: {', '.join(_prov_miss) or 'none'})"
)
st.markdown(
    f'<div style="background:{C["card"]};border-left:3px solid {_provider_label_color};'
    f'padding:7px 14px;font-family:monospace;font-size:12px;color:{C["text2"]};margin:2px 0">'
    f'<span style="color:{_provider_label_color};font-weight:700">[PROVIDER] </span>'
    f'{_provider_status}</div>',
    unsafe_allow_html=True,
)

# Instrument master status
_im         = _da.get("instrument_master", {})
_im_loaded  = _im.get("loaded", False)
_im_count   = _im.get("record_count", 0)
_im_src     = _im.get("source_name") or Path(_im.get("path", "instrument_master.csv")).name
_im_path    = _im.get("path", "data/instruments/instrument_master.csv")
_im_color   = C["success"] if _im_loaded else C["amber"]
_im_label   = (
    f"Loaded {_im_count} records from {_im_src}"
    if _im_loaded else
    (
        "NOT loaded — "
        + "place data/instruments/instrument_master.csv (canonical) "
        + "or data/instruments/kite_instruments.csv / angelone_instruments.csv (native). "
        + "Run: python scripts/check_instrument_master.py   "
        + "See docs/instrument_master.md."
    )
)
st.markdown(
    f'<div style="background:{C["card"]};border-left:3px solid {_im_color};'
    f'padding:7px 14px;font-family:monospace;font-size:12px;color:{C["text2"]};margin:2px 0">'
    f'<span style="color:{_im_color};font-weight:700">[INSTRUMENT MASTER] </span>{_im_label}</div>',
    unsafe_allow_html=True,
)

# Token readiness and coverage (only when master is loaded)
if _im_loaded:
    _cov = _im.get("coverage")   # CoverageReport | None

    # Build token readiness display from coverage (index + equity) or fallback to token_readiness
    if _cov is not None:
        _index_parts = []
        for _sym, _ok in _cov.index_ready.items():
            _c = C["success"] if _ok else C["amber"]
            _index_parts.append(f'<span style="color:{_c}">{_sym}:{"✓" if _ok else "✗"}</span>')

        _eq_parts = []
        for _sym, _ok in list(_cov.equity_ready.items())[:6]:  # show up to 6 stocks
            _c = C["success"] if _ok else C["neutral"]
            _eq_parts.append(f'<span style="color:{_c}">{_sym}:{"✓" if _ok else "—"}</span>')

        _all_parts = _index_parts + _eq_parts
        if _all_parts:
            st.markdown(
                f'<div style="background:{C["card"]};border-left:3px solid {C["neutral"]};'
                f'padding:7px 14px;font-family:monospace;font-size:12px;color:{C["text2"]};margin:2px 0">'
                f'<span style="color:{C["neutral"]};font-weight:700">[TOKEN READINESS] </span>'
                f'{_prov_name} — ' + "  ".join(_all_parts) + "</div>",
                unsafe_allow_html=True,
            )

        # Coverage breakdown
        if _cov.by_type:
            _type_summary = "  ".join(f"{k}:{v}" for k, v in sorted(_cov.by_type.items()))
            st.markdown(
                f'<div style="background:{C["card"]};border-left:3px solid {C["neutral"]};'
                f'padding:7px 14px;font-family:monospace;font-size:12px;color:{C["text2"]};margin:2px 0">'
                f'<span style="color:{C["neutral"]};font-weight:700">[IM COVERAGE] </span>'
                f'{_cov.total_count} rows for {_prov_name}  — {_type_summary}</div>',
                unsafe_allow_html=True,
            )

        # Expired option warnings
        if _cov.expired_option_symbols:
            _exp_names = ", ".join(_cov.expired_option_symbols[:4])
            if len(_cov.expired_option_symbols) > 4:
                _exp_names += f" +{len(_cov.expired_option_symbols) - 4} more"
            st.markdown(
                f'<div style="background:#180e00;border-left:3px solid {C["amber"]};'
                f'padding:7px 14px;font-family:monospace;font-size:12px;color:#ffffff;margin:2px 0">'
                f'<span style="color:{C["amber"]};font-weight:700">[EXPIRED OPTIONS] </span>'
                f'{len(_cov.expired_option_symbols)} expired contract(s) in instrument master: {_exp_names}. '
                f'Refresh master before next expiry.</div>',
                unsafe_allow_html=True,
            )
    else:
        # Fallback: simple token_readiness dict
        _tok_ready = _im.get("token_readiness", {})
        _tok_parts = []
        for _sym, _ok in _tok_ready.items():
            _c = C["success"] if _ok else C["amber"]
            _tok_parts.append(f'<span style="color:{_c}">{_sym}:{"✓" if _ok else "missing"}</span>')
        if _tok_parts:
            st.markdown(
                f'<div style="background:{C["card"]};border-left:3px solid {C["neutral"]};'
                f'padding:7px 14px;font-family:monospace;font-size:12px;color:{C["text2"]};margin:2px 0">'
                f'<span style="color:{C["neutral"]};font-weight:700">[TOKEN READINESS] </span>'
                f'Provider: {_prov_name} — ' + "  ".join(_tok_parts) + "</div>",
                unsafe_allow_html=True,
            )

    # Validation issues (only if there are errors or stale/missing-token warnings)
    _val = _im.get("validation")
    if _val is not None and (_val.has_errors or _val.missing_token_rows or _val.expired_option_count):
        _val_color = C["red"] if _val.has_errors else C["amber"]
        _val_parts = []
        if _val.has_errors:
            _val_parts.append(f'{len(_val.errors)} error(s)')
        if _val.missing_token_rows:
            _val_parts.append(f'{_val.missing_token_rows} missing token(s)')
        if _val.expired_option_count:
            _val_parts.append(f'{_val.expired_option_count} expired option(s)')
        st.markdown(
            f'<div style="background:{C["card"]};border-left:3px solid {_val_color};'
            f'padding:7px 14px;font-family:monospace;font-size:12px;color:{C["text2"]};margin:2px 0">'
            f'<span style="color:{_val_color};font-weight:700">[IM VALIDATION] </span>'
            + "  •  ".join(_val_parts)
            + "  — run: python scripts/check_instrument_master.py --verbose</div>",
            unsafe_allow_html=True,
        )

# Warn when broker provider is requested but credentials are incomplete
if _prov_fall and _prov_miss:
    _cred_warn = (
        f"Set the following environment variables to enable the {_prov_name} provider: "
        + ", ".join(_prov_miss)
        + ". See docs/broker_data_providers.md. Never commit secrets to source control."
    )
    st.markdown(
        f'<div style="background:#1a0800;border-left:3px solid {C["amber"]};'
        f'padding:8px 14px;font-family:monospace;font-size:12px;color:#ffffff;margin:2px 0">'
        f'<span style="color:{C["amber"]};font-weight:700">[CRED WARN] </span>{_cred_warn}</div>',
        unsafe_allow_html=True,
    )

# Data quality warnings (PCR contrarian, missing FII/DII, etc.)
if _dq["warnings"]:
    for _w in _dq["warnings"]:
        _w_color = C["amber"] if "PCR" in _w or "contrarian" in _w.lower() else C["red"]
        st.markdown(
            f'<div style="background:#180e00;border-left:3px solid {_w_color};'
            f'padding:8px 14px;font-family:monospace;font-size:12px;line-height:1.6;'
            f'color:#ffffff;margin:3px 0">'
            f'<span style="color:{_w_color};font-weight:700">[WARN] </span>{_w}</div>',
            unsafe_allow_html=True,
        )

if _dq["penalty_note"] and _dq["missing_layers"]:
    st.markdown(
        f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
        f'padding:8px 14px;font-family:monospace;font-size:12px;color:{C["amber"]};margin-top:4px">'
        f'{_dq["penalty_note"]}</div>',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# INTRADAY DATA MANAGER — upload and list 5m/15m/30m candle CSVs
# ---------------------------------------------------------------------------

_intraday_files = list_intraday_files()
_intraday_label = (
    f"[INTRADAY DATA] {len(_intraday_files)} file(s) loaded — click to manage"
    if _intraday_files
    else "[INTRADAY DATA] No candle files loaded — click to upload"
)

with st.expander(_intraday_label):
    st.markdown(
        f'<div style="font-family:monospace;font-size:12px;color:{C["text2"]};'
        f'line-height:1.7;margin-bottom:10px">'
        f'Intraday candles resolve SL vs target hit order (avoids OUTCOME_UNKNOWN). '
        f'Place files in <code>data/intraday/</code> or upload below.<br>'
        f'Naming: <code>NIFTY_20260520_5m.csv</code> — schema: '
        f'<code>symbol, datetime, open, high, low, close, volume, timeframe</code></div>',
        unsafe_allow_html=True,
    )

    # --- Upload widget ---
    _uploaded = st.file_uploader(
        "Upload intraday CSV",
        type=["csv"],
        key="intraday_upload",
        help="One symbol per file. Columns: symbol, datetime, open, high, low, close, volume, timeframe",
    )
    if _uploaded is not None:
        _save_result = save_uploaded_csv(_uploaded.getvalue())
        if _save_result["saved"]:
            _sv_stats = _save_result["stats"]
            st.success(
                f"Saved: {_save_result['filename']} — "
                f"{_sv_stats['rows']} rows | {_sv_stats['date_range']} | "
                f"timeframe: {', '.join(_sv_stats['timeframes'])}"
            )
            if _save_result["warnings"]:
                for _ww in _save_result["warnings"]:
                    st.warning(_ww)
            st.rerun()
        else:
            for _err in _save_result["errors"]:
                st.error(f"Upload rejected: {_err}")
            if _save_result["warnings"]:
                for _ww in _save_result["warnings"]:
                    st.warning(_ww)

    # --- File list ---
    if not _intraday_files:
        st.markdown(
            f'<div style="font-family:monospace;font-size:13px;color:{C["neutral"]};'
            f'padding:10px 0">No intraday CSV files found in data/intraday/</div>',
            unsafe_allow_html=True,
        )
    else:
        _valid_files   = [f for f in _intraday_files if f["valid"]]
        _invalid_files = [f for f in _intraday_files if not f["valid"]]

        # Valid files table
        if _valid_files:
            _rows_html = "".join(
                f'<tr style="border-bottom:1px solid {C["border"]}">'
                f'<td style="padding:5px 8px;color:{C["green"]};font-weight:700">'
                f'{", ".join(f["stats"].get("symbols", ["?"]))} </td>'
                f'<td style="padding:5px 8px;color:{C["text2"]}">'
                f'{", ".join(f["stats"].get("timeframes", ["?"]))} </td>'
                f'<td style="padding:5px 8px;color:{C["text2"]}">'
                f'{f["stats"].get("date_range", "—")} </td>'
                f'<td style="padding:5px 8px;color:{C["neutral"]}">'
                f'{f["stats"].get("rows", 0):,} rows </td>'
                f'<td style="padding:5px 8px;color:{C["neutral"]};font-size:11px">'
                f'{f["filename"]} </td>'
                f'</tr>'
                for f in _valid_files
            )
            st.markdown(
                f'<table style="width:100%;border-collapse:collapse;'
                f'font-family:monospace;font-size:12px">'
                f'<thead><tr style="border-bottom:1px solid {C["green"]}">'
                f'<th style="padding:4px 8px;color:{C["green"]};text-align:left">SYMBOL</th>'
                f'<th style="padding:4px 8px;color:{C["green"]};text-align:left">TF</th>'
                f'<th style="padding:4px 8px;color:{C["green"]};text-align:left">DATE RANGE</th>'
                f'<th style="padding:4px 8px;color:{C["green"]};text-align:left">ROWS</th>'
                f'<th style="padding:4px 8px;color:{C["green"]};text-align:left">FILE</th>'
                f'</tr></thead><tbody>{_rows_html}</tbody></table>',
                unsafe_allow_html=True,
            )
            st.markdown("<br>", unsafe_allow_html=True)

        # Today's analysis coverage
        _today_syms  = _da.get("intraday_symbols", [])
        _active_syms = [t.get("symbol","") for t in _all_actionable]
        if _active_syms:
            _cov_html = "".join(
                f'<div style="font-size:12px;font-family:monospace;padding:2px 0">'
                f'<span style="color:{"#4caf50" if s in _today_syms else C["amber"]}">'
                f'{"✓" if s in _today_syms else "—"} {s}: '
                f'{"intraday available — SL/target sequence confirmed" if s in _today_syms else "no intraday data — outcome may be OUTCOME_UNKNOWN"}'
                f'</span></div>'
                for s in _active_syms
            )
            st.markdown(
                f'<div style="font-size:11px;color:{C["neutral"]};font-family:monospace;'
                f'letter-spacing:1px;margin:8px 0 4px 0">TODAY\'S TRADE COVERAGE</div>'
                f'<div style="background:#0a100a;border:1px solid {C["border"]};'
                f'padding:8px 10px;border-radius:3px">{_cov_html}</div>',
                unsafe_allow_html=True,
            )

        # Invalid files warnings
        if _invalid_files:
            for _bf in _invalid_files:
                for _berr in _bf["errors"]:
                    st.error(f"{_bf['filename']}: {_berr}")

st.markdown("---")

# ---------------------------------------------------------------------------
# GLOBAL INTELLIGENCE — FIX 8: 80px cards, 20px value, 18px % change, border
# ---------------------------------------------------------------------------

INVERT_LABELS = {"Crude Oil WTI", "USD/INR"}
cue_list = [(lbl, d) for lbl, d in cues.items()
            if isinstance(d, dict) and d.get("last") is not None]

trades_all    = brief.get("trades", [])
stock_trades  = brief.get("stock_trades", [])
# Combined list of all actionable trades — used for intraday coverage checks
_all_actionable = trades_all + stock_trades

if cue_list:
    st.markdown(_section_header("GLOBAL INTELLIGENCE"), unsafe_allow_html=True)
    n = min(len(cue_list), 5)
    gcols = st.columns(n)
    for i, (lbl, d) in enumerate(cue_list[:n]):
        gcols[i].markdown(
            _cue_card(lbl, d.get("last", 0), d.get("pct_change") or 0,
                      invert=lbl in INVERT_LABELS),
            unsafe_allow_html=True,
        )

    # Opening gap / GIFT Nifty source line
    _og = cues.get("opening_gap", {})
    if _og and _og.get("value") is not None:
        _src_map = {
            "gift_nifty":  "GIFT Nifty (NSE API)",
            "nsei_proxy":  "^NSEI proxy (yfinance — GIFT unavailable)",
            "sp500_proxy": "ES=F proxy (S&P 500 Futures — regional markets closed)",
        }
        _og_src   = _og.get("source", "unknown")
        _og_label = _src_map.get(_og_src, _og_src)
        _og_val   = _og.get("value", 0)
        _og_pct   = _og.get("pct_change", 0) or 0
        _og_color = C["success"] if _og_pct >= 0 else C["red"]
        _og_arrow = "+" if _og_pct >= 0 else ""
        _og_src_color = C["success"] if _og_src == "gift_nifty" else C["amber"]
        st.markdown(
            f'<div style="background:{C["card"]};border-left:3px solid {_og_src_color};'
            f'padding:6px 12px;font-family:monospace;font-size:12px;color:{C["text2"]};margin:4px 0">'
            f'<span style="color:{_og_src_color};font-weight:700">[OPENING GAP] </span>'
            f'Nifty indicative: <span style="color:#fff;font-weight:700">{_og_val:,.1f}</span> '
            f'<span style="color:{_og_color}">({_og_arrow}{_og_pct:.2f}%)</span>  '
            f'<span style="color:{C["neutral"]}">Source: {_og_label}</span></div>',
            unsafe_allow_html=True,
        )

ic1, ic2 = st.columns(2)
with ic1:
    fig_vix = fetch_vix_trend()
    if fig_vix:
        st.plotly_chart(fig_vix, width="stretch", config={"displayModeBar": False})
    else:
        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
            f'padding:20px;text-align:center;font-family:monospace;font-size:14px;'
            f'color:{C["neutral"]}">[VIX TREND] Data unavailable</div>',
            unsafe_allow_html=True,
        )
with ic2:
    if trades_all and trades_all[0].get("confidence_breakdown"):
        st.plotly_chart(make_bias_donut(trades_all[0]["confidence_breakdown"]),
                        width="stretch", config={"displayModeBar": False})
    else:
        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
            f'padding:20px;text-align:center;font-family:monospace;font-size:14px;'
            f'color:{C["neutral"]}">[CONFIDENCE DONUT] Available when trades are recommended</div>',
            unsafe_allow_html=True,
        )

st.markdown("---")

# ---------------------------------------------------------------------------
# ACTIVE TRADE ORDERS — FIX 4: thick border, 28px price, 52px confidence, 12px bars
# ---------------------------------------------------------------------------

st.markdown(_section_header("ACTIVE TRADE ORDERS"), unsafe_allow_html=True)

if not trades_all:
    st.markdown(
        f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
        f'padding:22px;text-align:center;font-family:monospace;font-size:14px;'
        f'color:{C["neutral"]}">[NO ORDERS] No trades met confidence threshold (≥7.0) today</div>',
        unsafe_allow_html=True,
    )
else:
    for t in trades_all:
        sig  = t.get("signal", "?")
        sym  = t.get("symbol", "?")
        conf = t.get("confidence", 0)
        sig_c  = SIG_COLOR.get(sig, C["neutral"])
        conf_c = C["success"] if conf >= 8 else C["amber"] if conf >= 7 else C["red"]
        size   = t.get("position_size_recommendation", "HALF")
        size_c = C["success"] if size == "FULL" else C["amber"] if size == "HALF" else C["red"]
        rec_risk    = t.get("mid_session_recovery_risk", "")
        rec_high    = "HIGH" in str(rec_risk).upper()
        expiry_warn = t.get("expiry_day_warning", False)

        # Gate fields (present on new briefs; backward compat on old ones)
        gate_status  = t.get("gate_status", "TRADE_ALLOWED")
        gate_eff     = t.get("gate_effective_confidence")
        gate_orig    = t.get("gate_original_confidence", conf)
        gate_penalty = t.get("gate_data_penalty", 0)
        gate_cap     = t.get("gate_confidence_cap")
        GATE_COLORS  = {
            "TRADE_ALLOWED":     C["success"],
            "NO_TRADE":          C["amber"],
            "DATA_INSUFFICIENT": C["red"],
            "CONFLICTING_SIGNALS": C["red"],
        }

        # FIX 4 — 4px solid left border (red for PUT, green for CALL)
        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {sig_c}44;'
            f'border-left:4px solid {sig_c};border-radius:4px;'
            f'padding:18px;margin:10px 0">',
            unsafe_allow_html=True,
        )

        hc1, hc2, hc3 = st.columns([4, 2, 1])
        with hc1:
            gate_badge = (
                _badge(gate_status, GATE_COLORS.get(gate_status, C["neutral"]), 12)
                if gate_status != "TRADE_ALLOWED" else ""
            )
            # symbol at 28px bold
            st.markdown(
                f'<div style="font-size:28px;font-weight:bold;font-family:monospace;'
                f'color:{C["text"]};margin-bottom:6px">{sym}</div>'
                + _badge(sig, sig_c, 13)
                + _badge(f"STRIKE {t.get('strike','?')}", "#1a2e1a", 13)
                + _badge(t.get("expiry","?"), "#8b0000" if expiry_warn else "#1a2e1a", 13)
                + (" " + _badge("EXPIRY TODAY", C["red"], 13) if expiry_warn else "")
                + (" " + gate_badge if gate_badge else ""),
                unsafe_allow_html=True,
            )
        with hc2:
            cap_note = (
                f'<div style="font-size:11px;color:{C["amber"]};font-family:monospace;margin-top:4px">'
                f'eff: {gate_eff:.1f} (cap: {gate_cap})</div>'
                if gate_cap is not None and gate_eff is not None else (
                    f'<div style="font-size:11px;color:{C["amber"]};font-family:monospace;margin-top:4px">'
                    f'eff: {gate_eff:.1f} (-{gate_penalty:.1f})</div>'
                    if gate_penalty and gate_eff is not None else ""
                )
            )
            st.markdown(
                f'<div style="font-size:12px;color:{C["neutral"]};letter-spacing:1px;'
                f'font-family:monospace;margin-bottom:4px">POSITION SIZE</div>'
                + _badge(size, size_c, 13) + cap_note,
                unsafe_allow_html=True,
            )
        with hc3:
            # confidence at 48px right-aligned
            st.markdown(
                f'<div style="text-align:right">'
                f'<div style="font-size:12px;color:#c8e6c9;letter-spacing:1px;'
                f'font-family:monospace">CONFIDENCE</div>'
                f'<div style="font-size:48px;font-weight:bold;color:{conf_c};'
                f'font-family:monospace;line-height:1">{conf}</div>'
                f'<div style="font-size:12px;color:#c8e6c9;font-family:monospace">/ 10</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

        # FIX 4 — 5-column price grid, 70px min-height, 28px values
        pc = st.columns(5)
        pc[0].markdown(_price_box("ENTRY",     t.get("entry_price","?"),  C["text"]),    unsafe_allow_html=True)
        pc[1].markdown(_price_box("STOP LOSS", t.get("stop_loss","?"),    C["red"]),     unsafe_allow_html=True)
        pc[2].markdown(_price_box("TARGET 1",  t.get("target_1","?"),     C["green"]),   unsafe_allow_html=True)
        pc[3].markdown(_price_box("TARGET 2",  t.get("target_2","?"),     C["success"]), unsafe_allow_html=True)
        pc[4].markdown(_price_box("RISK:RWD",  f"1:{t.get('risk_reward','?')}", C["blue"]), unsafe_allow_html=True)

        # FIX 4 — separator line between price boxes and detail section
        st.markdown(
            f'<div style="margin:14px 0;border-top:1px solid {C["border"]}"></div>',
            unsafe_allow_html=True,
        )

        dc1, dc2 = st.columns(2)
        with dc1:
            st.markdown(
                f'<div style="font-size:12px;color:{C["neutral"]};letter-spacing:1px;'
                f'font-family:monospace;margin-bottom:6px">CONFIDENCE BREAKDOWN</div>',
                unsafe_allow_html=True,
            )
            bd = t.get("confidence_breakdown", {})
            # FIX 4 — 12px bars, 13px labels (via _conf_bar)
            st.markdown(
                f'<div>{"".join(_conf_bar(k,v) for k,v in bd.items())}</div>',
                unsafe_allow_html=True,
            )
            # Evidence checklist: which layers had valid data
            _e_rows = []
            _fii = meta.get("fii_dii", {})
            _fii_ok = not (_fii.get("fii_net_buy") == 0 and _fii.get("dii_net_buy") == 0)
            _poi_ok = "unavailable" not in str(ctx.get("smart_money_direction", "")).lower()
            _e_rows.append((
                "FII/DII Cash Flow",
                C["success"] if _fii_ok else C["red"],
                "Available" if _fii_ok else "ZERO — may be missing",
            ))
            _e_rows.append((
                "Participant Derivatives OI",
                C["success"] if _poi_ok else C["red"],
                "Available" if _poi_ok else "UNAVAILABLE",
            ))
            _e_rows.append(("Global Cues (yfinance)", C["success"], "Available"))
            _e_rows.append(("Technical Indicators",   C["success"], "Available"))
            _e_rows.append(("Option Chain PCR / OI",  C["success"], "Available"))
            _ev_html = "".join(
                f'<div style="display:flex;justify-content:space-between;font-size:12px;'
                f'font-family:monospace;padding:2px 0;color:{col}">'
                f'<span>{lbl}</span><span style="font-weight:700">{status}</span></div>'
                for lbl, col, status in _e_rows
            )
            st.markdown(
                f'<div style="font-size:12px;color:{C["neutral"]};letter-spacing:1px;'
                f'font-family:monospace;margin:10px 0 4px 0">EVIDENCE CHECKLIST</div>'
                + f'<div style="background:#0a100a;border:1px solid {C["border"]};'
                f'padding:8px 10px;border-radius:3px">{_ev_html}</div>',
                unsafe_allow_html=True,
            )

        with dc2:
            # FIX 4 — text boxes: 13px, line-height 1.6, 12px padding, colored borders
            trigger = t.get("entry_trigger", "")
            if trigger:
                st.markdown(
                    f'<div style="background:#1a1200;border-left:3px solid {C["amber"]};'
                    f'padding:12px;border-radius:3px;font-size:14px;line-height:1.8;'
                    f'margin:4px 0;font-family:monospace;color:#ffffff">'
                    f'<span style="color:{C["amber"]};font-weight:700">[TRIGGER]</span><br>'
                    f'{trigger}</div>',
                    unsafe_allow_html=True,
                )
            if rec_risk:
                rc = C["red"] if rec_high else C["success"]
                bg = "#1a0000" if rec_high else "#001a00"
                st.markdown(
                    f'<div style="background:{bg};border-left:3px solid {rc};'
                    f'padding:12px;border-radius:3px;font-size:14px;line-height:1.8;'
                    f'margin:4px 0;font-family:monospace;color:#ffffff">'
                    f'<span style="color:{rc};font-weight:700">[RECOVERY RISK]</span><br>'
                    f'{rec_risk}</div>',
                    unsafe_allow_html=True,
                )
            key_risk = t.get("key_risk", "")
            if key_risk:
                st.markdown(
                    f'<div style="background:#200000;border-left:3px solid {C["red"]};'
                    f'padding:12px;border-radius:3px;font-size:14px;line-height:1.8;'
                    f'margin:4px 0;font-family:monospace;color:#ffffff">'
                    f'<span style="color:{C["red"]};font-weight:700">[KEY RISK]</span><br>'
                    f'{key_risk}</div>',
                    unsafe_allow_html=True,
                )

        # FIX 2 — reasoning 14px line-height 1.6
        reasoning = t.get("reasoning", "")
        if reasoning:
            with st.expander("[REASONING] Expand analysis"):
                st.markdown(
                    f'<div style="font-size:14px;color:{C["text2"]};'
                    f'font-family:monospace;line-height:1.6">{reasoning}</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# STOCK SIGNALS — individual F&O stocks with confidence >= 7.0
# ---------------------------------------------------------------------------

STOCK_BLUE = "#40c4ff"
# stock_trades already loaded above alongside trades_all

st.markdown("---")
st.markdown(_section_header("STOCK SIGNALS (F&O UNIVERSE)"), unsafe_allow_html=True)

if not stock_trades:
    st.markdown(
        f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
        f'padding:22px;text-align:center;font-family:monospace;font-size:14px;'
        f'color:{C["neutral"]}">[NO STOCK SIGNALS] No individual stocks met the 7.0 '
        f'confidence threshold today. All 20 F&O stocks were analyzed.</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f'<div style="font-family:monospace;font-size:13px;color:{C["neutral"]};'
        f'margin-bottom:8px">{len(stock_trades)} stock signal(s) above 7.0 confidence '
        f'from 20-stock F&O universe</div>',
        unsafe_allow_html=True,
    )
    for st_trade in stock_trades:
        sym_s   = st_trade.get("symbol", "?")
        sig_s   = st_trade.get("signal", "?")
        conf_s  = st_trade.get("confidence", 0)
        sector  = st_trade.get("sector", "")
        aligned = st_trade.get("sector_aligned", False)
        size_s  = st_trade.get("position_size", "HALF")
        sig_sc  = SIG_COLOR.get(sig_s, STOCK_BLUE)
        conf_sc = C["success"] if conf_s >= 8 else C["amber"] if conf_s >= 7 else C["red"]
        size_sc = C["success"] if size_s == "FULL" else C["amber"] if size_s == "HALF" else C["red"]

        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {STOCK_BLUE}44;'
            f'border-left:4px solid {STOCK_BLUE};border-radius:4px;'
            f'padding:18px;margin:10px 0">',
            unsafe_allow_html=True,
        )

        sh1, sh2, sh3 = st.columns([4, 2, 1])
        with sh1:
            sector_badge  = _badge(sector, "#0d1e30", 13) if sector else ""
            aligned_badge = _badge("SECTOR ALIGNED", "#0d2a1a", 13) if aligned else ""
            st.markdown(
                f'<div style="font-size:32px;font-weight:bold;font-family:monospace;'
                f'color:{C["text"]};margin-bottom:6px">{sym_s}</div>'
                + _badge(sig_s, sig_sc, 13)
                + _badge("STOCK F&O", STOCK_BLUE, 13)
                + sector_badge
                + aligned_badge,
                unsafe_allow_html=True,
            )
        with sh2:
            st.markdown(
                f'<div style="font-size:12px;color:{C["neutral"]};letter-spacing:1px;'
                f'font-family:monospace;margin-bottom:4px">POSITION SIZE</div>'
                + _badge(size_s, size_sc, 13),
                unsafe_allow_html=True,
            )
        with sh3:
            st.markdown(
                f'<div style="text-align:right">'
                f'<div style="font-size:12px;color:{C["neutral"]};letter-spacing:1px;'
                f'font-family:monospace">CONFIDENCE</div>'
                f'<div style="font-size:56px;font-weight:bold;color:{conf_sc};'
                f'font-family:monospace;line-height:1">{conf_s}</div>'
                f'<div style="font-size:12px;color:{C["neutral"]};font-family:monospace">/ 10</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)

        sp = st.columns(5)
        sp[0].markdown(_price_box("ENTRY",     st_trade.get("entry_price","?"),  C["text"]),       unsafe_allow_html=True)
        sp[1].markdown(_price_box("STOP LOSS", st_trade.get("stop_loss","?"),    C["red"]),        unsafe_allow_html=True)
        sp[2].markdown(_price_box("TARGET 1",  st_trade.get("target_1","?"),     C["green"]),      unsafe_allow_html=True)
        sp[3].markdown(_price_box("TARGET 2",  st_trade.get("target_2","?"),     C["success"]),    unsafe_allow_html=True)
        sp[4].markdown(_price_box("RISK:RWD",  f"1:{st_trade.get('risk_reward','?')}", STOCK_BLUE), unsafe_allow_html=True)

        trigger_s = st_trade.get("trigger", "")
        if trigger_s:
            st.markdown(
                f'<div style="background:#001520;border-left:3px solid {STOCK_BLUE};'
                f'padding:12px;border-radius:3px;font-size:14px;line-height:1.8;'
                f'margin:8px 0;font-family:monospace;color:#ffffff">'
                f'<span style="color:{STOCK_BLUE};font-weight:700">[TRIGGER]</span><br>'
                f'{trigger_s}</div>',
                unsafe_allow_html=True,
            )

        reasoning_s = st_trade.get("reasoning", "")
        if reasoning_s:
            with st.expander(f"[ANALYSIS] {sym_s} trade reasoning"):
                st.markdown(
                    f'<div style="font-size:14px;color:{C["text2"]};'
                    f'font-family:monospace;line-height:1.6">{reasoning_s}</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("</div>", unsafe_allow_html=True)

filtered = brief.get("trades_filtered_out", [])
if filtered:
    with st.expander(f"[{len(filtered)} FILTERED] Below confidence threshold < 7.0"):
        for ft in filtered:
            sig_c = SIG_COLOR.get(ft.get("signal",""), C["neutral"])
            st.markdown(
                _badge(ft.get("symbol","?"), "#1a2e1a", 13) +
                _badge(ft.get("signal","?"), sig_c, 13) +
                _badge(f"CONF: {ft.get('confidence','?')}", C["neutral"], 13) +
                f'<span style="font-size:14px;color:{C["text2"]};font-family:monospace">'
                f'  {ft.get("filter_reason","")}</span>',
                unsafe_allow_html=True,
            )
            st.markdown(f'<hr style="border-color:{C["border"]};margin:6px 0">', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# BLOCKED TRADES — gated out by deterministic validation rules
# ---------------------------------------------------------------------------

_gated_out       = brief.get("trades_gated_out", [])
_stock_gated_out = brief.get("stock_trades_gated_out", [])
_gate_summary    = brief.get("_gate_summary", {})
_all_gated       = _gated_out + _stock_gated_out

if _all_gated:
    st.markdown(_section_header("BLOCKED TRADES — GATED OUT"), unsafe_allow_html=True)

    _gs_total_blocked = _gate_summary.get("blocked", 0) + _gate_summary.get("stock_blocked", 0)
    _gs_penalty       = _gate_summary.get("data_penalty", 0)
    st.markdown(
        f'<div style="font-family:monospace;font-size:13px;color:{C["amber"]};margin-bottom:8px">'
        f'{_gs_total_blocked} trade(s) blocked by validation gates. '
        f'Data quality penalty applied: {_gs_penalty:.1f} pts off confidence.</div>',
        unsafe_allow_html=True,
    )

    _GATE_STATUS_COLORS = {
        "CONFLICTING_SIGNALS": C["red"],
        "DATA_INSUFFICIENT":   "#ff9800",
        "NO_TRADE":            C["amber"],
    }
    for _gt in _all_gated:
        _gs_color = _GATE_STATUS_COLORS.get(_gt.get("gate_status",""), C["neutral"])
        _gs_sig   = _gt.get("signal","?")
        _gs_sig_c = SIG_COLOR.get(_gs_sig, C["neutral"])
        _is_stock = _gt in _stock_gated_out
        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {_gs_color}44;'
            f'border-left:4px solid {_gs_color};border-radius:4px;'
            f'padding:14px 16px;margin:6px 0">',
            unsafe_allow_html=True,
        )
        st.markdown(
            _badge(_gt.get("symbol","?"), "#1a2e1a", 13)
            + _badge(_gs_sig, _gs_sig_c, 13)
            + (_badge("STOCK F&O", STOCK_BLUE, 12) if _is_stock else "")
            + _badge(_gt.get("gate_status","?"), _gs_color, 13)
            + f'<span style="font-size:12px;color:{C["text2"]};font-family:monospace;margin-left:8px">'
            f'ORIGINAL CONF: {_gt.get("gate_original_confidence","?")} → '
            f'EFF: {_gt.get("gate_effective_confidence","?")}</span>',
            unsafe_allow_html=True,
        )
        _gr = _gt.get("gate_reason","")
        if _gr:
            st.markdown(
                f'<div style="font-size:13px;color:{_gs_color};font-family:monospace;'
                f'margin-top:6px;line-height:1.6">{_gr}</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

elif brief.get("_gates_applied") and not trades_all and not _all_gated:
    st.markdown(
        f'<div style="background:#180800;border-left:3px solid {C["amber"]};'
        f'padding:12px 16px;font-family:monospace;font-size:14px;color:#ffffff;margin:4px 0">'
        f'[GATES APPLIED] No trades passed validation. '
        f'Gate summary: penalty={_gate_summary.get("data_penalty",0):.1f} pts | '
        f'missing layers={_gate_summary.get("missing_layers",[])}</div>',
        unsafe_allow_html=True,
    )

# WATCHLIST ONLY — valid trades capped by max_trades_recommended
_watchlist_only = brief.get("watchlist_only", [])
if _watchlist_only:
    st.markdown(_section_header("WATCHLIST — CAPPED VALID IDEAS"), unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-family:monospace;font-size:13px;color:{C["neutral"]};margin-bottom:8px">'
        f'These trades passed all gates but exceed today\'s max_trades_recommended limit. '
        f'Monitor only — do not execute without reviewing the full brief.</div>',
        unsafe_allow_html=True,
    )
    for _wt in _watchlist_only:
        _wt_sig   = _wt.get("signal","?")
        _wt_sig_c = SIG_COLOR.get(_wt_sig, STOCK_BLUE)
        _wt_conf  = _wt.get("confidence", 0)
        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
            f'border-left:4px solid {C["neutral"]};border-radius:4px;'
            f'padding:10px 16px;margin:4px 0">'
            + _badge(_wt.get("symbol","?"), "#1a2e1a", 13)
            + _badge(_wt_sig, _wt_sig_c, 13)
            + _badge("WATCHLIST", C["neutral"], 12)
            + f'<span style="font-size:12px;color:{C["text2"]};font-family:monospace;margin-left:8px">'
            f'CONF: {_wt_conf} | '
            f'Capped by daily trade limit — review before acting</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

st.markdown("---")

# ---------------------------------------------------------------------------
# PERFORMANCE ANALYTICS — FIX 10: placeholder if <2 days of data
# ---------------------------------------------------------------------------

if trades_all or valid_log:
    st.markdown(_section_header("PERFORMANCE ANALYTICS"), unsafe_allow_html=True)
    ac1, ac2 = st.columns(2)

    with ac1:
        if trades_all:
            st.plotly_chart(make_radar_chart(trades_all),
                            width="stretch", config={"displayModeBar": False})
        else:
            st.markdown(
                f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
                f'padding:22px;text-align:center;font-family:monospace;font-size:14px;'
                f'color:{C["neutral"]}">[RADAR] Available when trades are recommended</div>',
                unsafe_allow_html=True,
            )

    with ac2:
        unique_days = len({r["date"] for r in valid_log}) if valid_log else 0
        if valid_log and unique_days >= 2:
            st.plotly_chart(make_accuracy_bar(valid_log),
                            width="stretch", config={"displayModeBar": False})
        else:
            # FIX 10 — friendly placeholder
            st.markdown(
                f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
                f'padding:28px 20px;text-align:center;font-family:monospace;'
                f'font-size:14px;color:{C["green"]};line-height:1.9">'
                f'◼ Accuracy tracking begins after first complete trading day.<br>'
                f'Run the agent through market close (3:30 PM IST) to start<br>'
                f'building your performance record.</div>',
                unsafe_allow_html=True,
            )

    # Backtest summary
    _bt_path = ROOT / "data" / "backtesting" / "results.json"
    if _bt_path.exists():
        _bt = json.loads(_bt_path.read_text(encoding="utf-8"))
        _bt_valid = [s for s in _bt.get("per_symbol", []) if "error" not in s]
        _bt_valid.sort(key=lambda x: x["accuracy_pct"], reverse=True)
        _top3 = _bt_valid[:3]
        _bot3 = _bt_valid[-3:]
        st.markdown(
            f'<div style="background:#0a100a;border:1px solid {C["border"]};'
            f'border-left:3px solid {C["green"]};padding:10px 14px;'
            f'font-family:monospace;font-size:13px;color:{C["text"]};margin-top:8px">'
            f'<span style="color:{C["green"]};font-weight:700">[BACKTEST] </span>'
            f'{_bt["days_requested"]}-day rule-based signals  |  '
            f'<span style="color:{C["amber"]};font-weight:700">'
            f'{_bt["overall_accuracy"]}% accuracy</span>  |  '
            f'{_bt["total_correct"]}/{_bt["total_signals"]} signals correct  |  '
            f'{_bt["symbols_ok"]}/{_bt["symbols_requested"]} symbols  '
            f'<span style="color:{C["neutral"]};font-size:11px">(run: {_bt.get("run_date","")})</span><br>'
            f'<span style="color:{C["success"]}">Top: '
            + "  ".join(f'{s["symbol"]} {s["accuracy_pct"]}%' for s in _top3)
            + f'</span>  <span style="color:{C["red"]}">Weak: '
            + "  ".join(f'{s["symbol"]} {s["accuracy_pct"]}%' for s in _bot3)
            + f'</span></div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

# ---------------------------------------------------------------------------
# PAPER TRADE JOURNAL
# ---------------------------------------------------------------------------

_journal_path = ROOT / "data" / "paper_trades" / "journal.json"
st.markdown(_section_header("PAPER TRADE JOURNAL"), unsafe_allow_html=True)

_TIER_PILL_COLOR = {
    "PRIMARY_CANDIDATE":   C["success"],
    "SECONDARY_CANDIDATE": C["amber"],
    "WATCHLIST_REVIEW":    C["neutral"],
    "HIGH":                C["success"],
    "MID":                 C["amber"],
    "LOW":                 C["red"],
    "BELOW_FLOOR":         C["red"],
}

if _journal_path.exists():
    _j = json.loads(_journal_path.read_text(encoding="utf-8"))
    _jtrades = _j.get("trades", [])
    if _jtrades:
        # Build display table
        _rows = []
        for _jt in _jtrades:
            _rows.append({
                "Date":      _jt.get("date", ""),
                "Symbol":    _jt.get("symbol", ""),
                "Signal":    _jt.get("signal", ""),
                "Conf":      _jt.get("confidence", ""),
                "Eff Conf":  _jt.get("gate_effective_confidence", ""),
                "Tier":      _jt.get("confidence_tier", ""),
                "Entry":     _jt.get("entry_price", ""),
                "SL":        _jt.get("stop_loss", ""),
                "T1":        _jt.get("target_1", ""),
                "T2":        _jt.get("target_2", ""),
                "Status":    _jt.get("status", ""),
                "Outcome":   (_jt.get("outcome") or "—").replace("_", " "),
                "Direction": _jt.get("signal", ""),
                "Notes":     ("PAPER" if _jt.get("paper_only") else ""),
            })
        _jdf = pd.DataFrame(_rows)
        st.dataframe(_jdf, use_container_width=True, hide_index=True)

        # Tier badges row
        _tier_pills = "".join(
            _pill(_jt.get("confidence_tier", ""), _TIER_PILL_COLOR.get(
                _jt.get("confidence_tier", ""), C["neutral"]
            ), 11)
            for _jt in _jtrades
        )
        if _tier_pills:
            st.markdown(
                f'<div style="margin:4px 0 8px 0;font-size:11px;color:{C["text2"]};'
                f'font-family:monospace">Confidence tiers: {_tier_pills}</div>',
                unsafe_allow_html=True,
            )

        # Running stats
        _completed = [t for t in _jtrades if t.get("status") == "CLOSED"]
        _pending   = [t for t in _jtrades if t.get("status") == "OPEN"]
        _wins      = [t for t in _completed if t.get("outcome") in ("TARGET_1_HIT", "TARGET_2_HIT")]
        _t1_hits   = [t for t in _completed if t.get("outcome") == "TARGET_1_HIT"]
        _confs     = [t.get("gate_effective_confidence") for t in _jtrades
                      if isinstance(t.get("gate_effective_confidence"), (int, float))]
        _avg_conf  = round(sum(_confs) / len(_confs), 2) if _confs else None
        _tier_counts = {}
        for _t in _jtrades:
            _tc = _t.get("confidence_tier", "—")
            _tier_counts[_tc] = _tier_counts.get(_tc, 0) + 1

        _tier_str = "  ".join(
            _pill(f"{k}: {v}", _TIER_PILL_COLOR.get(k, C["neutral"]), 11)
            for k, v in _tier_counts.items()
        )
        _win_pct = (
            f"{len(_wins)/len(_completed)*100:.0f}%"
            if _completed else "—"
        )
        _t1_pct = (
            f"{len(_t1_hits)/len(_completed)*100:.0f}%"
            if _completed else "—"
        )

        _stats_html = (
            f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
            f'padding:10px 14px;font-family:monospace;font-size:13px;'
            f'color:{C["text"]};margin-top:8px;line-height:1.8">'
            f'Total: <b>{len(_jtrades)}</b>  |  '
            f'Completed: <b>{len(_completed)}</b>  |  '
            f'Pending: <b>{len(_pending)}</b>  |  '
            f'Win% (T1+T2): <b>{_win_pct}</b>  |  '
            f'T1 hit%: <b>{_t1_pct}</b>  |  '
            f'Avg conf: <b>{_avg_conf if _avg_conf is not None else "—"}</b>'
            f'<br>Tiers: {_tier_str}'
        )
        if len(_completed) < 10:
            _needed = 10 - len(_completed)
            _stats_html += (
                f'<br><span style="color:{C["amber"]}">'
                f'collecting data — {len(_completed)} of 10 trades needed '
                f'({_needed} more to go)</span>'
            )
        _stats_html += "</div>"
        st.markdown(_stats_html, unsafe_allow_html=True)

        # Signal quality expandable row detail
        _sq_path = ROOT / "data" / "analytics" / "signal_quality.json"
        _sq_map  = {}
        if _sq_path.exists():
            try:
                _sq_data = json.loads(_sq_path.read_text(encoding="utf-8"))
                for _sq in _sq_data.get("trades", []):
                    _sq_key = f"{_sq.get('date','')}-{_sq.get('symbol','')}"
                    _sq_map[_sq_key] = _sq
            except Exception:
                pass

        if _sq_map:
            st.markdown(
                f'<div style="font-size:11px;color:{C["text2"]};font-family:monospace;'
                f'letter-spacing:1px;margin:8px 0 4px 0">SIGNAL QUALITY DETAILS</div>',
                unsafe_allow_html=True,
            )
            for _jt in _jtrades:
                _sq_key = f"{_jt.get('date','')}-{_jt.get('symbol','')}"
                _sq = _sq_map.get(_sq_key)
                if not _sq:
                    continue
                _sq_oq    = _sq.get("outcome_quality", "—")
                _sq_color = {
                    "CLEAN_WIN":      C["success"],
                    "PENDING":        C["amber"],
                    "STOPPED_OUT":    C["red"],
                    "SCRATCHED":      C["neutral"],
                    "OUTCOME_UNKNOWN": C["neutral"],
                }.get(_sq_oq, C["neutral"])
                _sq_label = f"{_jt.get('symbol','')} {_jt.get('signal','')} [{_jt.get('date','')}]"
                with st.expander(_sq_label):
                    _sq_cols = st.columns(4)
                    _sq_cols[0].metric("Outcome Quality", _sq_oq)
                    _sq_cols[1].metric("Best PnL",
                        f"+{_sq['best_case_pnl']:.2f}" if _sq.get("best_case_pnl") is not None else "—")
                    _sq_cols[2].metric("Worst PnL",
                        f"{_sq['worst_case_pnl']:.2f}" if _sq.get("worst_case_pnl") is not None else "—")
                    _sq_cols[3].metric("Intraday", "Yes" if _sq.get("intraday_available") else "No")
                    if _sq.get("time_to_sl") is not None:
                        st.caption(f"SL hit at: {_sq['time_to_sl']} min from open")
                    if _sq.get("time_to_t1") is not None:
                        st.caption(f"T1 hit at: {_sq['time_to_t1']} min from open")

    else:
        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
            f'padding:14px;font-family:monospace;font-size:14px;color:{C["neutral"]}">'
            f'[EMPTY] Journal exists but contains no trades yet.</div>',
            unsafe_allow_html=True,
        )
else:
    st.markdown(
        f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
        f'padding:14px;font-family:monospace;font-size:14px;color:{C["neutral"]}">'
        f'[PENDING] Paper trade journal will appear after the first morning analysis run.</div>',
        unsafe_allow_html=True,
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# 9:00 AM PRE-OPEN CONFIRMATION
# ---------------------------------------------------------------------------

st.markdown(_section_header("PRE-OPEN CONFIRMATION (9:00 AM)"), unsafe_allow_html=True)

if preopen:
    pc = st.columns(3)
    pc[0].metric("VIX (FRESH)",     preopen.get("vix_current", "—"))
    pc[1].metric("GLOBAL BIAS",     preopen.get("global_bias_current", "—"))
    pc[2].metric("OPENING OUTLOOK", preopen.get("market_opening_outlook", "—"))

    for tr in preopen.get("trades_review", []):
        action = tr.get("final_action", "?")
        ac     = ACTION_COLOR.get(action, C["neutral"])
        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {ac}44;'
            f'border-left:4px solid {ac};border-radius:4px;'
            f'padding:12px 16px;margin:6px 0;display:flex;align-items:center;gap:16px">'
            f'<div style="font-size:24px;font-weight:bold;color:{ac};'
            f'min-width:90px;font-family:monospace">[{action}]</div>'
            f'<div>'
            f'<div style="font-weight:700;font-size:16px;font-family:monospace;'
            f'color:{C["text"]}">'
            f'{tr.get("symbol","?")} {tr.get("signal","?")} {tr.get("strike","")}</div>'
            f'<div style="font-size:14px;color:{C["text2"]};font-family:monospace">'
            f'{tr.get("final_action_reason","")}</div>'
            + (f'<div style="font-size:13px;color:{C["text2"]};margin-top:3px;'
               f'font-family:monospace">[NOTE] {tr["note"]}</div>' if tr.get("note") else "")
            + (f'<div style="font-size:13px;color:{C["amber"]};margin-top:3px;'
               f'font-family:monospace">[ADJ ENTRY] {tr["adjusted_entry"]}</div>'
               if tr.get("adjusted_entry") else "")
            + f'</div></div>',
            unsafe_allow_html=True,
        )

    if preopen.get("preopen_summary"):
        st.markdown(
            f'<div style="background:{C["card2"]};border:1px solid {C["border"]};'
            f'padding:12px 16px;font-family:monospace;font-size:14px;'
            f'color:{C["text2"]};margin-top:8px;line-height:1.6">'
            f'{preopen["preopen_summary"]}</div>',
            unsafe_allow_html=True,
        )
else:
    st.markdown(
        f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
        f'padding:14px;font-family:monospace;font-size:14px;color:{C["neutral"]}">'
        f'[PENDING] Pre-open confirmation appears here after 09:00 AM IST.</div>',
        unsafe_allow_html=True,
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# POST-MARKET DEBRIEF
# ---------------------------------------------------------------------------

if pm_data and not pm_data.get("error"):
    st.markdown(_section_header("POST-MARKET DEBRIEF"), unsafe_allow_html=True)

    grade = pm_data.get("overall_grade", "?")
    gc    = GRADE_COLOR.get(grade, C["neutral"])

    dg1, dg2 = st.columns([1, 3])
    with dg1:
        st.markdown(
            f'<div style="background:{C["card"]};border:2px solid {gc};'
            f'border-radius:6px;padding:16px;text-align:center">'
            f'<div style="font-size:13px;color:{C["neutral"]};letter-spacing:1px;'
            f'font-family:monospace">TODAY\'S GRADE</div>'
            f'<div style="font-size:56px;font-weight:bold;color:{gc};'
            f'font-family:monospace;line-height:1.1">{grade}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with dg2:
        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
            f'padding:12px;font-family:monospace;font-size:14px;'
            f'color:{C["text2"]};margin-bottom:8px;line-height:1.6">'
            f'{pm_data.get("accuracy_summary","")}</div>',
            unsafe_allow_html=True,
        )
        for tg in pm_data.get("trade_grades", []):
            tgc = GRADE_COLOR.get(tg.get("grade","?"), C["neutral"])
            st.markdown(
                _badge(tg.get("symbol","?"), "#1a2e1a", 13) +
                _badge(tg.get("signal","?"), SIG_COLOR.get(tg.get("signal",""), C["neutral"]), 13) +
                _badge((tg.get("outcome","?") or "?").replace("_"," "), C["neutral"], 13) +
                _badge(f"GRADE: {tg.get('grade','?')}", tgc, 13) +
                f'<span style="font-size:13px;color:{C["text2"]};font-family:monospace">'
                f'  {tg.get("grade_reasoning","")}</span>',
                unsafe_allow_html=True,
            )

    tb = pm_data.get("tomorrow_bias", {})
    if tb:
        adj   = tb.get("confidence_adjustment", 0)
        adj_c = C["success"] if adj >= 0 else C["red"]
        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
            f'border-left:3px solid {adj_c};padding:12px 16px;margin-top:8px;'
            f'font-family:monospace;font-size:14px;line-height:1.6">'
            f'<span style="color:{C["green"]};font-weight:700">TOMORROW BIAS: </span>'
            f'<span style="color:{adj_c};font-weight:700">{adj:+.1f}</span>'
            f' — {tb.get("adjustment_reason","")}'
            f'<br><span style="color:{C["amber"]}">[REMEMBER] </span>'
            f'{tb.get("key_reminder","")}</div>',
            unsafe_allow_html=True,
        )

    ww = pm_data.get("what_worked", [])
    wf = pm_data.get("what_failed", [])
    if ww or wf:
        wc1, wc2 = st.columns(2)
        with wc1:
            if ww:
                items = "".join(
                    f'<div style="font-size:13px;color:{C["text2"]};'
                    f'font-family:monospace;padding:2px 0">✓ {w}</div>' for w in ww
                )
                st.markdown(
                    f'<div style="font-size:12px;color:{C["success"]};font-family:monospace;'
                    f'letter-spacing:1px;margin-bottom:4px">WHAT WORKED</div>' + items,
                    unsafe_allow_html=True,
                )
        with wc2:
            if wf:
                items = "".join(
                    f'<div style="font-size:13px;color:{C["text2"]};'
                    f'font-family:monospace;padding:2px 0">✗ {w}</div>' for w in wf
                )
                st.markdown(
                    f'<div style="font-size:12px;color:{C["red"]};font-family:monospace;'
                    f'letter-spacing:1px;margin-bottom:4px">WHAT FAILED</div>' + items,
                    unsafe_allow_html=True,
                )

    st.markdown("---")

# ---------------------------------------------------------------------------
# KEY SUPPORT / RESISTANCE
# ---------------------------------------------------------------------------

st.markdown(_section_header("KEY SUPPORT / RESISTANCE"), unsafe_allow_html=True)
levels = brief.get("key_levels", {})

kc1, kc2 = st.columns(2)
with kc1:
    sup_h = "".join(_level_chip(v, "support")    for v in levels.get("nifty_support", []))
    res_h = "".join(_level_chip(v, "resistance") for v in levels.get("nifty_resistance", []))
    st.markdown(
        f'<div style="font-size:15px;color:{C["text"]};font-family:monospace;'
        f'letter-spacing:1px;margin-bottom:8px;font-weight:700">NIFTY</div>'
        f'<span style="font-size:15px;color:{C["neutral"]};font-family:monospace">SUPPORT &nbsp;&nbsp;</span>{sup_h}'
        f'<br><span style="font-size:15px;color:{C["neutral"]};font-family:monospace">RESISTANCE </span>{res_h}',
        unsafe_allow_html=True,
    )
with kc2:
    sup_h = "".join(_level_chip(v, "support")    for v in levels.get("banknifty_support", []))
    res_h = "".join(_level_chip(v, "resistance") for v in levels.get("banknifty_resistance", []))
    st.markdown(
        f'<div style="font-size:15px;color:{C["text"]};font-family:monospace;'
        f'letter-spacing:1px;margin-bottom:8px;font-weight:700">BANKNIFTY</div>'
        f'<span style="font-size:15px;color:{C["neutral"]};font-family:monospace">SUPPORT &nbsp;&nbsp;</span>{sup_h}'
        f'<br><span style="font-size:15px;color:{C["neutral"]};font-family:monospace">RESISTANCE </span>{res_h}',
        unsafe_allow_html=True,
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# SECTOR ROTATION
# ---------------------------------------------------------------------------

avoid = brief.get("sectors_to_avoid_today", [])
favor = brief.get("sectors_to_favor_today", [])
watch = brief.get("stocks_to_watch", [])

if avoid or favor or watch:
    st.markdown(_section_header("SECTOR ROTATION"), unsafe_allow_html=True)
    sc1, sc2 = st.columns(2)
    with sc1:
        st.markdown(
            f'<div style="font-size:13px;color:{C["red"]};font-family:monospace;'
            f'letter-spacing:1px;margin-bottom:6px">AVOID TODAY</div>'
            + (" ".join(_pill(s[:50], C["red"], 14) for s in avoid)
               if avoid else f'<span style="font-size:14px;color:{C["neutral"]}">—</span>'),
            unsafe_allow_html=True,
        )
    with sc2:
        st.markdown(
            f'<div style="font-size:13px;color:{C["success"]};font-family:monospace;'
            f'letter-spacing:1px;margin-bottom:6px">FAVOR TODAY</div>'
            + (" ".join(_pill(s[:50], C["success"], 14) for s in favor)
               if favor else f'<span style="font-size:14px;color:{C["neutral"]}">—</span>'),
            unsafe_allow_html=True,
        )
    if watch:
        with st.expander("[WATCHLIST]"):
            for s in watch:
                st.markdown(
                    f'<span style="font-size:13px;color:{C["text2"]};'
                    f'font-family:monospace">• {s}</span>',
                    unsafe_allow_html=True,
                )
    st.markdown("---")

# ---------------------------------------------------------------------------
# PREDICTION ACCURACY
# ---------------------------------------------------------------------------

st.markdown(_section_header("PREDICTION ACCURACY"), unsafe_allow_html=True)

if not valid_log:
    st.markdown(
        f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
        f'padding:14px;font-family:monospace;font-size:14px;color:{C["neutral"]}">'
        f'[PENDING] No tracked outcomes yet. Auto-runs at 15:30 IST.</div>',
        unsafe_allow_html=True,
    )
else:
    total   = len(valid_log)
    correct = sum(1 for r in valid_log if r.get("was_correct"))
    t1      = sum(1 for r in valid_log if r.get("outcome") == "TARGET_1_HIT")
    t2      = sum(1 for r in valid_log if r.get("outcome") == "TARGET_2_HIT")
    sl      = sum(1 for r in valid_log if r.get("outcome") == "SL_HIT")
    unk     = sum(1 for r in valid_log if r.get("outcome") == "OUTCOME_UNKNOWN")

    am = st.columns(6)
    am[0].metric("TRADES",    total)
    am[1].metric("DIRECTION", f"{correct/total*100:.0f}%", f"{correct}/{total}")
    am[2].metric("TARGET 1",  f"{t1/total*100:.0f}%",      f"{t1}/{total}")
    am[3].metric("TARGET 2",  f"{t2/total*100:.0f}%",      f"{t2}/{total}")
    am[4].metric("SL HIT",    f"{sl/total*100:.0f}%",      f"{sl}/{total}")
    am[5].metric("UNKNOWN",   unk, delta="need intraday data" if unk else None,
                 delta_color="off")

    with st.expander("[LOG] Full outcome history"):
        df_log = pd.DataFrame(valid_log)
        cols   = ["date","symbol","signal","confidence",
                  "predicted_direction","actual_direction",
                  "was_correct","outcome","day_change_pct",
                  "sl_hit","target_1_reached","target_2_reached"]
        if "claude_grade" in df_log.columns:
            cols.append("claude_grade")
        if "path_note" in df_log.columns:
            cols.append("path_note")
        st.dataframe(
            df_log[[c for c in cols if c in df_log.columns]].sort_values("date", ascending=False),
            use_container_width=True,
        )

    # Ambiguity warnings in accuracy log
    _ambiguous    = [r for r in valid_log if r.get("outcome") == "OUTCOME_UNKNOWN"]
    _intraday_src = [r for r in valid_log if r.get("data_source") == "INTRADAY"]
    if _ambiguous:
        st.markdown(
            f'<div style="background:#1a0f00;border-left:3px solid {C["amber"]};'
            f'padding:10px 14px;font-family:monospace;font-size:13px;line-height:1.7;'
            f'color:#ffffff;margin-top:6px">'
            f'<span style="color:{C["amber"]};font-weight:700">[OUTCOME AUDIT] </span>'
            f'{len(_ambiguous)} trade(s) marked OUTCOME_UNKNOWN: both SL and target triggered via '
            f'daily OHLC H/L extremes — intraday fill sequence cannot be confirmed. '
            f'These are excluded from win rate. '
            f'Fix: provide 5m CSV files in data/intraday/ (see docs/data_requirements.md).</div>',
            unsafe_allow_html=True,
        )
    if _intraday_src:
        st.markdown(
            f'<div style="background:#001500;border-left:3px solid {C["success"]};'
            f'padding:8px 14px;font-family:monospace;font-size:12px;color:#ffffff;margin-top:4px">'
            f'<span style="color:{C["success"]};font-weight:700">[INTRADAY CONFIRMED] </span>'
            f'{len(_intraday_src)} trade outcome(s) resolved from intraday candle sequence '
            f'(fill order confirmed — not from OHLC extremes).</div>',
            unsafe_allow_html=True,
        )

    # Entry trigger feasibility check for historical trades
    _trigger_audits = []
    for _r in valid_log:
        _trade_ref = next(
            (t for t in trades_all if t.get("symbol") == _r.get("symbol")), None
        )
        if _trade_ref and _r.get("index_open"):
            _tc = check_entry_trigger_met(_trade_ref, _r.get("index_open"))
            if _tc["met"] is not None:
                _trigger_audits.append({
                    "symbol": _r["symbol"],
                    "date":   _r["date"],
                    "check":  _tc,
                    "was_correct": _r.get("was_correct"),
                })
    if _trigger_audits:
        with st.expander("[TRIGGER AUDIT] Entry trigger feasibility check"):
            for _ta in _trigger_audits:
                _tc_col = C["success"] if _ta["check"]["met"] else C["red"]
                st.markdown(
                    f'<div style="font-size:13px;font-family:monospace;padding:3px 0;color:{_tc_col}">'
                    f'{_ta["symbol"]} ({_ta["date"]}): {_ta["check"]["reason"]}'
                    + (f' | Direction was {"CORRECT" if _ta["was_correct"] else "WRONG"}' if _ta["was_correct"] is not None else "")
                    + f'</div>',
                    unsafe_allow_html=True,
                )

# ---------------------------------------------------------------------------
# MORNING SUMMARY
# ---------------------------------------------------------------------------

summary           = brief.get("morning_summary", "")
post_gate_summary = brief.get("post_gate_summary", "")
_trading_ok       = brief.get("market_context", {}).get("trading_recommended", True)

if summary or post_gate_summary:
    st.markdown("---")
    # Show post-gate summary prominently when trades were blocked
    if post_gate_summary and not _trading_ok:
        st.markdown(
            f'<div style="background:#1a0800;border:1px solid {C["amber"]}44;'
            f'border-left:3px solid {C["amber"]};padding:16px;'
            f'font-family:monospace;font-size:15px;color:{C["text2"]};line-height:1.9;margin-bottom:6px">'
            f'<span style="color:{C["amber"]};font-weight:700;letter-spacing:1px">'
            f'[POST-GATE STATUS] </span>{post_gate_summary}</div>',
            unsafe_allow_html=True,
        )
    if summary:
        _sum_label   = "[CLAUDE ANALYSIS — PRE-GATE]" if post_gate_summary and not _trading_ok else "[MORNING BRIEF]"
        _sum_border  = C["neutral"] if (post_gate_summary and not _trading_ok) else C["green"]
        st.markdown(
            f'<div style="background:{C["card"]};border:1px solid {C["border"]};'
            f'border-left:3px solid {_sum_border};padding:16px;'
            f'font-family:monospace;font-size:15px;color:{C["text2"]};line-height:1.9">'
            f'<span style="color:{_sum_border};font-weight:700;letter-spacing:1px">'
            f'{_sum_label} </span>{summary}</div>',
            unsafe_allow_html=True,
        )
