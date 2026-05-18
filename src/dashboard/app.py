"""
AI Trade Dashboard — Military Command Center
Smart refresh: JS 300s countdown + file mtime detection.
Military color palette with Plotly charts.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    layout="wide",
    page_title="COMMAND CENTER // AI TRADE INTEL",
    page_icon="🎯",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT      = Path(__file__).parent.parent.parent
DATA_DIR  = ROOT / "data" / "processed"
IST       = pytz.timezone("Asia/Kolkata")
TODAY_STR = datetime.now(IST).strftime("%Y-%m-%d")

# Military palette
MIL = {
    "bg":      "#0a0f08",
    "card":    "#0d1a0b",
    "card2":   "#111e0e",
    "green":   "#8bc34a",
    "amber":   "#ffc107",
    "red":     "#ff5252",
    "success": "#4caf50",
    "neutral": "#546e7a",
    "text":    "#e8f5e9",
    "text2":   "#90a4ae",
    "border":  "#1c3319",
}

BIAS_COLOR   = {"BULLISH": MIL["success"], "BEARISH": MIL["red"],     "MIXED": MIL["amber"]}
VIX_COLOR    = {"LOW": MIL["success"], "NORMAL": MIL["green"],
                "ELEVATED": MIL["amber"], "HIGH": MIL["red"], "CRISIS": "#b71c1c"}
RISK_COLOR   = {"LOW": MIL["success"], "MEDIUM": MIL["amber"],
                "HIGH": MIL["red"], "VERY_HIGH": "#b71c1c"}
SIG_COLOR    = {"CALL": MIL["success"], "PUT": MIL["red"], "NEUTRAL": MIL["neutral"]}
ACTION_COLOR = {"GO": MIL["success"], "WAIT": MIL["amber"], "SKIP": MIL["red"]}
GRADE_COLOR  = {"A": MIL["success"], "B": MIL["green"], "C": MIL["amber"],
                "D": "#ff9800", "F": MIL["red"]}

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _badge(text: str, bg: str = "#333", size: int = 13) -> str:
    return (
        f'<span style="background:{bg};color:#fff;padding:3px 12px;'
        f'border-radius:2px;font-weight:700;font-size:{size}px;'
        f'margin-right:5px;letter-spacing:0.5px;white-space:nowrap;'
        f'font-family:monospace">{text}</span>'
    )

def _pill(text: str, color: str = "#555") -> str:
    return (
        f'<span style="background:{color}22;color:{color};'
        f'border:1px solid {color}55;padding:2px 10px;border-radius:2px;'
        f'font-size:12px;margin:2px 3px;display:inline-block;font-family:monospace">'
        f'{text}</span>'
    )

def _conf_bar(label: str, value: float, max_val: int = 10) -> str:
    pct   = min(value / max_val * 100, 100)
    color = MIL["red"] if value < 5 else MIL["amber"] if value < 7 else MIL["green"]
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
        f'<div style="margin:4px 0">'
        f'<div style="display:flex;justify-content:space-between;'
        f'font-size:10px;color:{MIL["text2"]};font-family:monospace">'
        f'<span>{short}</span>'
        f'<span style="color:{color}">{value}/10</span></div>'
        f'<div style="background:#1a2e1a;border-radius:1px;height:5px">'
        f'<div style="background:{color};width:{pct}%;height:5px;border-radius:1px">'
        f'</div></div></div>'
    )

def _cue_card(label: str, last: float, pct: float, invert: bool = False) -> str:
    arrow = "▲" if pct >= 0 else "▼"
    color = MIL["green"] if pct >= 0 else MIL["red"]
    if invert:
        color = MIL["red"] if pct >= 0 else MIL["green"]
    return (
        f'<div style="background:{MIL["card"]};border:1px solid {MIL["border"]};'
        f'padding:10px 8px;border-radius:2px;text-align:center">'
        f'<div style="font-size:9px;color:{MIL["text2"]};letter-spacing:1px;'
        f'font-family:monospace;margin-bottom:3px">{label.upper()}</div>'
        f'<div style="font-size:15px;font-weight:700;color:{MIL["text"]};'
        f'font-family:monospace">{last:,.2f}</div>'
        f'<div style="font-size:12px;color:{color};font-weight:600;font-family:monospace">'
        f'{arrow} {pct:+.2f}%</div></div>'
    )

def _price_box(label: str, val, color: str) -> str:
    return (
        f'<div style="background:{color}11;border:1px solid {color}44;'
        f'border-radius:2px;padding:8px;text-align:center">'
        f'<div style="font-size:9px;color:{MIL["text2"]};letter-spacing:1px;'
        f'font-family:monospace">{label}</div>'
        f'<div style="font-size:17px;font-weight:700;color:{color};'
        f'font-family:monospace">{val}</div></div>'
    )

def _level_chip(val, kind: str) -> str:
    color = MIL["success"] if kind == "support" else MIL["red"]
    return _pill(f"{val:,.0f}", color)

def _section_header(title: str) -> str:
    return (
        f'<div style="border-left:3px solid {MIL["green"]};padding:3px 12px;'
        f'margin:16px 0 10px 0;font-family:monospace;letter-spacing:1px;'
        f'color:{MIL["green"]};font-size:13px;font-weight:700">{title}</div>'
    )

# ---------------------------------------------------------------------------
# Plotly chart builders
# ---------------------------------------------------------------------------

def _plotly_layout(title: str, height: int = 240) -> dict:
    return dict(
        paper_bgcolor=MIL["card"],
        plot_bgcolor=MIL["bg"],
        font=dict(color=MIL["text"], family="monospace"),
        margin=dict(l=10, r=40, t=38, b=10),
        title=dict(text=title, font=dict(size=11, color=MIL["green"]), x=0.5),
        height=height,
    )


def make_bias_donut(confidence_breakdown: dict) -> go.Figure:
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
    colors = [MIL["red"] if v < 5 else MIL["amber"] if v < 7 else MIL["green"] for v in values]
    avg    = sum(values) / len(values) if values else 0

    fig = go.Figure(go.Pie(
        labels=labels,
        values=values,
        hole=0.62,
        marker=dict(colors=colors, line=dict(color=MIL["bg"], width=1)),
        textinfo="label+value",
        textfont=dict(size=9, color=MIL["text"]),
        hovertemplate="%{label}: %{value}/10<extra></extra>",
    ))
    layout = _plotly_layout("CONFIDENCE INTEL")
    layout["showlegend"] = False
    layout["annotations"] = [dict(
        text=f"<b>{avg:.1f}</b><br>/10",
        x=0.5, y=0.5,
        font=dict(size=16, color=MIL["green"]),
        showarrow=False,
    )]
    fig.update_layout(**layout)
    return fig


@st.cache_data(ttl=3600)
def fetch_vix_trend() -> go.Figure | None:
    import yfinance as yf
    try:
        hist = yf.Ticker("^INDIAVIX").history(period="14d", interval="1d")
        if hist.empty:
            return None
        dates  = [d.strftime("%d %b") for d in hist.index]
        values = hist["Close"].round(2).tolist()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dates, y=values,
            mode="lines+markers",
            line=dict(color=MIL["amber"], width=2),
            marker=dict(size=5, color=MIL["amber"]),
            fill="tozeroy",
            fillcolor="rgba(255,193,7,0.08)",
            hovertemplate="%{x}: VIX %{y:.2f}<extra></extra>",
        ))
        for lvl, col, lbl in [
            (13, MIL["success"], "NORMAL 13"),
            (17, MIL["amber"],   "ELEVATED 17"),
            (22, MIL["red"],     "HIGH 22"),
        ]:
            fig.add_hline(
                y=lvl, line_dash="dash", line_color=col, line_width=1,
                annotation_text=lbl,
                annotation_font=dict(size=8, color=col),
                annotation_position="right",
            )
        layout = _plotly_layout("INDIA VIX — 14 DAY TREND")
        layout["xaxis"] = dict(gridcolor=MIL["border"], tickfont=dict(size=9))
        layout["yaxis"] = dict(gridcolor=MIL["border"], tickfont=dict(size=9))
        layout["showlegend"] = False
        fig.update_layout(**layout)
        return fig
    except Exception:
        return None


def make_radar_chart(trades: list[dict]) -> go.Figure:
    cats = ["GLOBAL", "INSTITUTIONAL", "DERIVATIVES",
            "TECHNICALS", "VOLUME", "NEWS", "EVENTS", "R:R"]
    keys = ["global_cues", "institutional_flow", "derivative_signals",
            "technical_analysis", "volume_confirmation", "news_sentiment",
            "event_risk", "risk_reward_quality"]
    colors = [MIL["green"], MIL["amber"], "#42a5f5"]

    fig = go.Figure()
    for i, t in enumerate(trades[:3]):
        bd   = t.get("confidence_breakdown", {})
        vals = [bd.get(k, 0) for k in keys]
        col  = colors[i % len(colors)]
        fig.add_trace(go.Scatterpolar(
            r=vals + [vals[0]],
            theta=cats + [cats[0]],
            fill="toself",
            fillcolor=col + "22",
            line=dict(color=col, width=2),
            name=f"{t.get('symbol')} {t.get('signal')}",
            hovertemplate="%{theta}: %{r}/10<extra></extra>",
        ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True, range=[0, 10],
                gridcolor=MIL["border"], color=MIL["text2"],
                tickfont=dict(size=8),
            ),
            angularaxis=dict(gridcolor=MIL["border"], color=MIL["text2"], tickfont=dict(size=9)),
            bgcolor=MIL["bg"],
        ),
        paper_bgcolor=MIL["card"],
        font=dict(color=MIL["text"], family="monospace"),
        margin=dict(l=20, r=20, t=38, b=30),
        title=dict(text="INTELLIGENCE RADAR", font=dict(size=11, color=MIL["green"]), x=0.5),
        legend=dict(
            font=dict(size=9), bgcolor=MIL["card"],
            x=0.5, xanchor="center", y=-0.12, orientation="h",
        ),
        showlegend=len(trades) > 1,
        height=300,
    )
    return fig


def make_accuracy_bar(valid_log: list[dict]) -> go.Figure:
    df = pd.DataFrame(valid_log)
    df["correct_int"] = df["was_correct"].astype(int)
    df["wrong_int"]   = (~df["was_correct"]).astype(int)
    chart_df = (
        df.groupby("date")[["correct_int", "wrong_int"]]
        .sum().tail(7)
        .rename(columns={"correct_int": "Correct", "wrong_int": "Wrong"})
    )
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=chart_df.index, y=chart_df["Correct"],
        name="CORRECT", marker_color=MIL["success"],
        hovertemplate="%{x}<br>Correct: %{y}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=chart_df.index, y=chart_df["Wrong"],
        name="WRONG", marker_color=MIL["red"],
        hovertemplate="%{x}<br>Wrong: %{y}<extra></extra>",
    ))
    layout = _plotly_layout("7-DAY DIRECTION ACCURACY", height=300)
    layout["barmode"]    = "group"
    layout["xaxis"]      = dict(gridcolor=MIL["border"], showgrid=False, tickfont=dict(size=9))
    layout["yaxis"]      = dict(gridcolor=MIL["border"], tickformat="d", tickfont=dict(size=9))
    layout["legend"]     = dict(font=dict(size=9), bgcolor=MIL["card"])
    layout["showlegend"] = True
    fig.update_layout(**layout)
    return fig


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

ctx  = brief.get("market_context", {})
meta = brief.get("_meta", {})
cues = meta.get("global_cues", {})

# ---------------------------------------------------------------------------
# Smart refresh — detect file changes, JS countdown
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
# CSS — Military theme
# ---------------------------------------------------------------------------

st.markdown(f"""
<style>
  body, .main, .stApp {{
    background-color: {MIL["bg"]} !important;
    color: {MIL["text"]};
  }}
  .block-container {{ padding-top: 0.4rem !important; }}
  h1, h2, h3 {{ color: {MIL["green"]} !important; font-family: monospace !important; }}
  h1 {{ font-size: 1.3rem !important; }}
  h2 {{ font-size: 1rem !important; border-bottom: 1px solid {MIL["border"]}; padding-bottom: 3px; }}
  .stMetric label {{
    font-size: 9px !important;
    color: {MIL["text2"]} !important;
    letter-spacing: 1px !important;
    font-family: monospace !important;
    text-transform: uppercase !important;
  }}
  .stMetric [data-testid="stMetricValue"] {{
    font-size: 18px !important;
    font-weight: 700 !important;
    color: {MIL["text"]} !important;
    font-family: monospace !important;
  }}
  div[data-testid="stExpander"] {{
    border: 1px solid {MIL["border"]} !important;
    border-radius: 2px !important;
  }}
  .stButton > button {{
    background: {MIL["card2"]} !important;
    color: {MIL["green"]} !important;
    border: 1px solid {MIL["green"]} !important;
    border-radius: 2px !important;
    font-family: monospace !important;
    letter-spacing: 1px !important;
    padding: 4px 14px !important;
    font-size: 12px !important;
  }}
  .stButton > button:hover {{ background: {MIL["green"]}22 !important; }}
  hr {{ border-color: {MIL["border"]} !important; margin: 8px 0 !important; }}
  .stDataFrame, .stDataFrame * {{ background: {MIL["card"]} !important; color: {MIL["text2"]} !important; }}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# HEADER
# ---------------------------------------------------------------------------

bias  = ctx.get("overall_market_bias", "MIXED")
vzone = ctx.get("vix_zone", "NORMAL")
risk  = ctx.get("overall_risk_rating", "MEDIUM")
t_ok  = ctx.get("trading_recommended", True)

analysis_time = brief.get("analysis_time", "08:00 IST")
date_raw      = brief.get("date", TODAY_STR)
try:
    display_date = datetime.strptime(date_raw, "%Y-%m-%d").strftime("%d %b %Y — %A").upper()
except Exception:
    display_date = date_raw.upper()

now_str = datetime.now(IST).strftime("%H:%M:%S IST")

# Top bar with JS countdown timer
st.markdown(f"""
<div style="background:{MIL["card2"]};border-bottom:2px solid {MIL["border"]};
padding:7px 16px;margin-bottom:6px;display:flex;justify-content:space-between;
align-items:center;flex-wrap:wrap;gap:8px">
  <div>
    <span style="font-family:monospace;font-size:17px;font-weight:900;
    color:{MIL["green"]};letter-spacing:2px">COMMAND CENTER</span>
    <span style="font-family:monospace;font-size:10px;color:{MIL["text2"]};
    margin-left:12px">// AI TRADE INTELLIGENCE // {display_date}</span>
  </div>
  <div style="display:flex;align-items:center;gap:14px">
    <span style="font-family:monospace;font-size:10px;color:{MIL["text2"]}">
      BRIEF: {analysis_time} | NOW: {now_str}
    </span>
    <span id="cc-timer" style="font-family:monospace;font-size:10px;color:{MIL["amber"]}"></span>
  </div>
</div>
<script>
(function(){{
  var cd = 300;
  function tick(){{
    var el = document.getElementById('cc-timer');
    if(el) el.textContent = '[SYNC IN ' + cd + 's]';
    cd--;
    if(cd < 0){{ window.location.reload(); }} else {{ setTimeout(tick, 1000); }}
  }}
  tick();
}})();
</script>
""", unsafe_allow_html=True)

# Badges row + manual refresh button
hc1, hc2 = st.columns([7, 1])
with hc1:
    deploy_lbl = "DEPLOY" if t_ok else "STAND DOWN"
    st.markdown(
        _badge(bias,        BIAS_COLOR.get(bias, MIL["neutral"]), 13) +
        _badge(f"VIX: {vzone}", VIX_COLOR.get(vzone, MIL["neutral"]), 13) +
        _badge(f"RISK: {risk}", RISK_COLOR.get(risk, MIL["neutral"]), 13) +
        _badge(deploy_lbl,  MIL["success"] if t_ok else MIL["red"], 13) +
        (f' <span style="font-size:11px;color:{MIL["amber"]};font-family:monospace">'
         f'⚠ {ctx["trading_caution"]}</span>' if ctx.get("trading_caution") else ""),
        unsafe_allow_html=True,
    )
with hc2:
    if st.button("↻ SYNC"):
        st.rerun()

# Alerts
for w in brief.get("event_warnings", []):
    st.markdown(
        f'<div style="background:#2a1a00;border-left:3px solid {MIL["amber"]};'
        f'padding:6px 12px;font-family:monospace;font-size:11px;margin:3px 0">'
        f'[ALERT] {w}</div>',
        unsafe_allow_html=True,
    )
if any(t.get("expiry_day_warning") for t in brief.get("trades", [])):
    st.markdown(
        f'<div style="background:#200000;border-left:3px solid {MIL["red"]};'
        f'padding:6px 12px;font-family:monospace;font-size:11px;margin:3px 0">'
        f'[EXPIRY] OPTIONS EXPIRE TODAY — EXIT / ROLL ALL POSITIONS BEFORE 13:00 IST</div>',
        unsafe_allow_html=True,
    )

st.divider()

# ---------------------------------------------------------------------------
# MARKET SNAPSHOT
# ---------------------------------------------------------------------------

st.markdown(_section_header("▌ MARKET SNAPSHOT"), unsafe_allow_html=True)

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
mc[6].metric("FII NET (Cr)",
             f"₹{fii_net:+,.0f}" if isinstance(fii_net, (int,float)) else "—",
             delta_color="normal" if isinstance(fii_net, (int,float)) and fii_net >= 0 else "inverse")

st.divider()

# ---------------------------------------------------------------------------
# GLOBAL INTELLIGENCE + VIX TREND + DONUT
# ---------------------------------------------------------------------------

INVERT_LABELS = {"Crude Oil WTI", "USD/INR"}
cue_list = [(lbl, d) for lbl, d in cues.items()
            if isinstance(d, dict) and d.get("last") is not None]

if cue_list:
    st.markdown(_section_header("▌ GLOBAL INTELLIGENCE"), unsafe_allow_html=True)
    n = min(len(cue_list), 5)
    gcols = st.columns(n)
    for i, (lbl, d) in enumerate(cue_list[:n]):
        gcols[i].markdown(
            _cue_card(lbl, d.get("last", 0), d.get("pct_change") or 0,
                      invert=lbl in INVERT_LABELS),
            unsafe_allow_html=True,
        )

trades_all = brief.get("trades", [])

ic1, ic2 = st.columns(2)
with ic1:
    fig_vix = fetch_vix_trend()
    if fig_vix:
        st.plotly_chart(fig_vix, use_container_width=True, config={"displayModeBar": False})
    else:
        st.info("[VIX TREND] Data unavailable")

with ic2:
    if trades_all:
        bd = trades_all[0].get("confidence_breakdown", {})
        if bd:
            st.plotly_chart(make_bias_donut(bd), use_container_width=True,
                            config={"displayModeBar": False})
    else:
        st.info("[CONFIDENCE DONUT] Appears when trades are recommended")

st.divider()

# ---------------------------------------------------------------------------
# ACTIVE TRADE ORDERS
# ---------------------------------------------------------------------------

st.markdown(_section_header("▌ ACTIVE TRADE ORDERS"), unsafe_allow_html=True)

if not trades_all:
    st.markdown(
        f'<div style="background:{MIL["card"]};border:1px solid {MIL["border"]};'
        f'padding:16px;text-align:center;font-family:monospace;font-size:12px;'
        f'color:{MIL["text2"]}">[NO ORDERS] No trades met confidence threshold (≥7.0) today</div>',
        unsafe_allow_html=True,
    )
else:
    for t in trades_all:
        sig  = t.get("signal", "?")
        sym  = t.get("symbol", "?")
        conf = t.get("confidence", 0)
        sig_c  = SIG_COLOR.get(sig, MIL["neutral"])
        conf_c = MIL["success"] if conf >= 8 else MIL["amber"] if conf >= 7 else MIL["red"]
        size   = t.get("position_size_recommendation", "HALF")
        size_c = MIL["success"] if size == "FULL" else MIL["amber"] if size == "HALF" else MIL["red"]
        rec_risk    = t.get("mid_session_recovery_risk", "")
        rec_high    = "HIGH" in str(rec_risk).upper()
        expiry_warn = t.get("expiry_day_warning", False)

        st.markdown(
            f'<div style="background:{MIL["card"]};border:1px solid {sig_c}44;'
            f'border-left:4px solid {sig_c};border-radius:2px;padding:14px;margin:8px 0">',
            unsafe_allow_html=True,
        )

        hc1, hc2, hc3 = st.columns([4, 2, 1])
        with hc1:
            st.markdown(
                f'<div style="font-size:20px;font-weight:900;font-family:monospace;'
                f'color:{MIL["text"]};margin-bottom:4px">{sym}</div>'
                + _badge(sig, sig_c, 14)
                + _badge(f"STRIKE {t.get('strike','?')}", MIL["card2"], 12)
                + _badge(t.get("expiry","?"), "#8b0000" if expiry_warn else MIL["card2"], 12)
                + (" " + _badge("EXPIRY TODAY", MIL["red"], 12) if expiry_warn else ""),
                unsafe_allow_html=True,
            )
        with hc2:
            st.markdown(
                f'<div style="font-size:9px;color:{MIL["text2"]};letter-spacing:1px;'
                f'font-family:monospace;margin-bottom:3px">POSITION SIZE</div>'
                + _badge(size, size_c, 13),
                unsafe_allow_html=True,
            )
        with hc3:
            st.markdown(
                f'<div style="text-align:right">'
                f'<div style="font-size:9px;color:{MIL["text2"]};letter-spacing:1px;'
                f'font-family:monospace">CONFIDENCE</div>'
                f'<div style="font-size:30px;font-weight:900;color:{conf_c};'
                f'font-family:monospace">{conf}</div>'
                f'<div style="font-size:9px;color:{MIL["text2"]};font-family:monospace">/ 10</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)

        pc = st.columns(5)
        pc[0].markdown(_price_box("ENTRY",     t.get("entry_price","?"),  MIL["text"]),   unsafe_allow_html=True)
        pc[1].markdown(_price_box("STOP LOSS", t.get("stop_loss","?"),    MIL["red"]),    unsafe_allow_html=True)
        pc[2].markdown(_price_box("TARGET 1",  t.get("target_1","?"),     MIL["green"]),  unsafe_allow_html=True)
        pc[3].markdown(_price_box("TARGET 2",  t.get("target_2","?"),     MIL["success"]),unsafe_allow_html=True)
        pc[4].markdown(_price_box("RISK:RWD",  f"1:{t.get('risk_reward','?')}", "#42a5f5"), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        dc1, dc2 = st.columns(2)
        with dc1:
            st.markdown(
                f'<div style="font-size:9px;color:{MIL["text2"]};letter-spacing:1px;'
                f'font-family:monospace;margin-bottom:4px">CONFIDENCE BREAKDOWN</div>',
                unsafe_allow_html=True,
            )
            bd = t.get("confidence_breakdown", {})
            st.markdown(
                f'<div>{"".join(_conf_bar(k,v) for k,v in bd.items())}</div>',
                unsafe_allow_html=True,
            )

        with dc2:
            trigger = t.get("entry_trigger", "")
            if trigger:
                st.markdown(
                    f'<div style="background:#1a1200;border-left:2px solid {MIL["amber"]};'
                    f'padding:7px 10px;border-radius:2px;font-size:11px;margin:3px 0;'
                    f'font-family:monospace">[TRIGGER] {trigger}</div>',
                    unsafe_allow_html=True,
                )
            if rec_risk:
                rc = MIL["red"] if rec_high else MIL["success"]
                st.markdown(
                    f'<div style="background:{"#1a0000" if rec_high else "#001a00"};'
                    f'border-left:2px solid {rc};padding:7px 10px;border-radius:2px;'
                    f'font-size:11px;margin:3px 0;font-family:monospace">'
                    f'[RECOVERY RISK] {rec_risk}</div>',
                    unsafe_allow_html=True,
                )
            key_risk = t.get("key_risk", "")
            if key_risk:
                st.markdown(
                    f'<div style="background:#200000;border-left:2px solid {MIL["red"]};'
                    f'padding:7px 10px;border-radius:2px;font-size:11px;margin:3px 0;'
                    f'font-family:monospace">[KEY RISK] {key_risk}</div>',
                    unsafe_allow_html=True,
                )

        reasoning = t.get("reasoning", "")
        if reasoning:
            with st.expander("[REASONING] Expand analysis"):
                st.markdown(
                    f'<div style="font-size:12px;color:{MIL["text2"]};'
                    f'font-family:monospace;line-height:1.6">{reasoning}</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("</div>", unsafe_allow_html=True)

filtered = brief.get("trades_filtered_out", [])
if filtered:
    with st.expander(f"[{len(filtered)} FILTERED] Below confidence threshold < 7.0"):
        for ft in filtered:
            sig_c = SIG_COLOR.get(ft.get("signal",""), MIL["neutral"])
            st.markdown(
                _badge(ft.get("symbol","?"), MIL["card2"], 12) +
                _badge(ft.get("signal","?"), sig_c, 12) +
                _badge(f"CONF: {ft.get('confidence','?')}", MIL["neutral"], 12) +
                f'<span style="font-size:11px;color:{MIL["text2"]};font-family:monospace">'
                f'  {ft.get("filter_reason","")}</span>',
                unsafe_allow_html=True,
            )
            st.markdown(f'<hr style="border-color:{MIL["border"]};margin:4px 0">', unsafe_allow_html=True)

st.divider()

# ---------------------------------------------------------------------------
# PERFORMANCE ANALYTICS: Radar + 7-day accuracy
# ---------------------------------------------------------------------------

valid_log = [r for r in acc_log if not r.get("error")]

if trades_all or valid_log:
    st.markdown(_section_header("▌ PERFORMANCE ANALYTICS"), unsafe_allow_html=True)
    ac1, ac2 = st.columns(2)
    with ac1:
        if trades_all:
            st.plotly_chart(make_radar_chart(trades_all), use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.info("[RADAR] Available when trades are recommended")
    with ac2:
        if valid_log:
            st.plotly_chart(make_accuracy_bar(valid_log), use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.info("[ACCURACY] No outcomes tracked yet — runs at 15:30 IST")
    st.divider()

# ---------------------------------------------------------------------------
# 9:00 AM PRE-OPEN CONFIRMATION
# ---------------------------------------------------------------------------

st.markdown(_section_header("▌ PRE-OPEN CONFIRMATION (9:00 AM)"), unsafe_allow_html=True)

if preopen:
    pc = st.columns(3)
    pc[0].metric("VIX (FRESH)",     preopen.get("vix_current", "—"))
    pc[1].metric("GLOBAL BIAS",     preopen.get("global_bias_current", "—"))
    pc[2].metric("OPENING OUTLOOK", preopen.get("market_opening_outlook", "—"))

    for tr in preopen.get("trades_review", []):
        action = tr.get("final_action", "?")
        ac     = ACTION_COLOR.get(action, MIL["neutral"])
        st.markdown(
            f'<div style="background:{MIL["card"]};border:1px solid {ac}44;'
            f'border-left:4px solid {ac};border-radius:2px;'
            f'padding:10px 14px;margin:5px 0;display:flex;align-items:center;gap:14px">'
            f'<div style="font-size:22px;font-weight:900;color:{ac};'
            f'min-width:80px;font-family:monospace">[{action}]</div>'
            f'<div>'
            f'<div style="font-weight:700;font-size:14px;font-family:monospace;'
            f'color:{MIL["text"]}">'
            f'{tr.get("symbol","?")} {tr.get("signal","?")} {tr.get("strike","")}</div>'
            f'<div style="font-size:12px;color:{MIL["text2"]};font-family:monospace">'
            f'{tr.get("final_action_reason","")}</div>'
            + (f'<div style="font-size:11px;color:{MIL["text2"]};margin-top:2px;'
               f'font-family:monospace">[NOTE] {tr["note"]}</div>' if tr.get("note") else "")
            + (f'<div style="font-size:11px;color:{MIL["amber"]};margin-top:2px;'
               f'font-family:monospace">[ADJ ENTRY] {tr["adjusted_entry"]}</div>'
               if tr.get("adjusted_entry") else "")
            + f'</div></div>',
            unsafe_allow_html=True,
        )

    if preopen.get("preopen_summary"):
        st.markdown(
            f'<div style="background:{MIL["card2"]};border:1px solid {MIL["border"]};'
            f'padding:10px 14px;font-family:monospace;font-size:12px;'
            f'color:{MIL["text2"]};margin-top:6px">'
            f'{preopen["preopen_summary"]}</div>',
            unsafe_allow_html=True,
        )
else:
    st.markdown(
        f'<div style="background:{MIL["card"]};border:1px solid {MIL["border"]};'
        f'padding:12px;font-family:monospace;font-size:11px;color:{MIL["text2"]}">'
        f'[PENDING] Pre-open confirmation appears here after 09:00 AM IST.</div>',
        unsafe_allow_html=True,
    )

st.divider()

# ---------------------------------------------------------------------------
# POST-MARKET DEBRIEF
# ---------------------------------------------------------------------------

if pm_data and not pm_data.get("error"):
    st.markdown(_section_header("▌ POST-MARKET DEBRIEF"), unsafe_allow_html=True)

    grade = pm_data.get("overall_grade", "?")
    gc    = GRADE_COLOR.get(grade, MIL["neutral"])

    dg1, dg2 = st.columns([1, 3])
    with dg1:
        st.markdown(
            f'<div style="background:{MIL["card"]};border:2px solid {gc};'
            f'border-radius:3px;padding:14px;text-align:center">'
            f'<div style="font-size:9px;color:{MIL["text2"]};letter-spacing:1px;'
            f'font-family:monospace">TODAY\'S GRADE</div>'
            f'<div style="font-size:52px;font-weight:900;color:{gc};'
            f'font-family:monospace;line-height:1.1">{grade}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with dg2:
        st.markdown(
            f'<div style="background:{MIL["card"]};border:1px solid {MIL["border"]};'
            f'padding:10px;font-family:monospace;font-size:12px;'
            f'color:{MIL["text2"]};margin-bottom:6px">'
            f'{pm_data.get("accuracy_summary","")}</div>',
            unsafe_allow_html=True,
        )
        for tg in pm_data.get("trade_grades", []):
            tgc = GRADE_COLOR.get(tg.get("grade","?"), MIL["neutral"])
            st.markdown(
                _badge(tg.get("symbol","?"), MIL["card2"], 12) +
                _badge(tg.get("signal","?"), SIG_COLOR.get(tg.get("signal",""), MIL["neutral"]), 12) +
                _badge((tg.get("outcome","?") or "?").replace("_"," "), MIL["neutral"], 12) +
                _badge(f"GRADE: {tg.get('grade','?')}", tgc, 12) +
                f'<span style="font-size:11px;color:{MIL["text2"]};font-family:monospace">'
                f'  {tg.get("grade_reasoning","")}</span>',
                unsafe_allow_html=True,
            )

    tb = pm_data.get("tomorrow_bias", {})
    if tb:
        adj   = tb.get("confidence_adjustment", 0)
        adj_c = MIL["success"] if adj >= 0 else MIL["red"]
        st.markdown(
            f'<div style="background:{MIL["card"]};border:1px solid {MIL["border"]};'
            f'border-left:3px solid {adj_c};padding:10px 14px;margin-top:6px;'
            f'font-family:monospace;font-size:12px">'
            f'<span style="color:{MIL["green"]};font-weight:700">TOMORROW BIAS: </span>'
            f'<span style="color:{adj_c};font-weight:700">{adj:+.1f}</span>'
            f' — {tb.get("adjustment_reason","")}'
            f'<br><span style="color:{MIL["amber"]}">[REMEMBER] </span>'
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
                    f'<div style="font-size:11px;color:{MIL["text2"]};'
                    f'font-family:monospace;padding:1px 0">✓ {w}</div>' for w in ww
                )
                st.markdown(
                    f'<div style="font-size:9px;color:{MIL["success"]};font-family:monospace;'
                    f'letter-spacing:1px;margin-bottom:3px">WHAT WORKED</div>' + items,
                    unsafe_allow_html=True,
                )
        with wc2:
            if wf:
                items = "".join(
                    f'<div style="font-size:11px;color:{MIL["text2"]};'
                    f'font-family:monospace;padding:1px 0">✗ {w}</div>' for w in wf
                )
                st.markdown(
                    f'<div style="font-size:9px;color:{MIL["red"]};font-family:monospace;'
                    f'letter-spacing:1px;margin-bottom:3px">WHAT FAILED</div>' + items,
                    unsafe_allow_html=True,
                )

    st.divider()

# ---------------------------------------------------------------------------
# KEY LEVELS
# ---------------------------------------------------------------------------

st.markdown(_section_header("▌ KEY SUPPORT / RESISTANCE"), unsafe_allow_html=True)
levels = brief.get("key_levels", {})

kc1, kc2 = st.columns(2)
with kc1:
    sup_h = "".join(_level_chip(v, "support")    for v in levels.get("nifty_support", []))
    res_h = "".join(_level_chip(v, "resistance") for v in levels.get("nifty_resistance", []))
    st.markdown(
        f'<div style="font-size:9px;color:{MIL["text2"]};font-family:monospace;'
        f'letter-spacing:1px;margin-bottom:4px">NIFTY</div>'
        f'<span style="font-size:10px;color:{MIL["text2"]};font-family:monospace">SUP </span>{sup_h}'
        f'<br><span style="font-size:10px;color:{MIL["text2"]};font-family:monospace">RES </span>{res_h}',
        unsafe_allow_html=True,
    )
with kc2:
    sup_h = "".join(_level_chip(v, "support")    for v in levels.get("banknifty_support", []))
    res_h = "".join(_level_chip(v, "resistance") for v in levels.get("banknifty_resistance", []))
    st.markdown(
        f'<div style="font-size:9px;color:{MIL["text2"]};font-family:monospace;'
        f'letter-spacing:1px;margin-bottom:4px">BANKNIFTY</div>'
        f'<span style="font-size:10px;color:{MIL["text2"]};font-family:monospace">SUP </span>{sup_h}'
        f'<br><span style="font-size:10px;color:{MIL["text2"]};font-family:monospace">RES </span>{res_h}',
        unsafe_allow_html=True,
    )

st.divider()

# ---------------------------------------------------------------------------
# SECTORS
# ---------------------------------------------------------------------------

avoid = brief.get("sectors_to_avoid_today", [])
favor = brief.get("sectors_to_favor_today", [])
watch = brief.get("stocks_to_watch", [])

if avoid or favor or watch:
    st.markdown(_section_header("▌ SECTOR ROTATION"), unsafe_allow_html=True)
    sc1, sc2 = st.columns(2)
    with sc1:
        st.markdown(
            f'<div style="font-size:9px;color:{MIL["red"]};font-family:monospace;'
            f'letter-spacing:1px;margin-bottom:4px">AVOID TODAY</div>'
            + (" ".join(_pill(s[:50], MIL["red"]) for s in avoid)
               if avoid else f'<span style="font-size:11px;color:{MIL["text2"]}">—</span>'),
            unsafe_allow_html=True,
        )
    with sc2:
        st.markdown(
            f'<div style="font-size:9px;color:{MIL["success"]};font-family:monospace;'
            f'letter-spacing:1px;margin-bottom:4px">FAVOR TODAY</div>'
            + (" ".join(_pill(s[:50], MIL["success"]) for s in favor)
               if favor else f'<span style="font-size:11px;color:{MIL["text2"]}">—</span>'),
            unsafe_allow_html=True,
        )
    if watch:
        with st.expander("[WATCHLIST]"):
            for s in watch:
                st.markdown(
                    f'<span style="font-size:12px;color:{MIL["text2"]};'
                    f'font-family:monospace">• {s}</span>',
                    unsafe_allow_html=True,
                )
    st.divider()

# ---------------------------------------------------------------------------
# ACCURACY TRACKER
# ---------------------------------------------------------------------------

st.markdown(_section_header("▌ PREDICTION ACCURACY"), unsafe_allow_html=True)

if not valid_log:
    st.markdown(
        f'<div style="background:{MIL["card"]};border:1px solid {MIL["border"]};'
        f'padding:12px;font-family:monospace;font-size:11px;color:{MIL["text2"]}">'
        f'[PENDING] No tracked outcomes yet. Auto-runs at 15:30 IST.</div>',
        unsafe_allow_html=True,
    )
else:
    total   = len(valid_log)
    correct = sum(1 for r in valid_log if r.get("was_correct"))
    t1      = sum(1 for r in valid_log if r.get("target_1_reached"))
    t2      = sum(1 for r in valid_log if r.get("target_2_reached"))
    sl      = sum(1 for r in valid_log if r.get("sl_hit"))

    am = st.columns(5)
    am[0].metric("TRADES",    total)
    am[1].metric("DIRECTION", f"{correct/total*100:.0f}%", f"{correct}/{total}")
    am[2].metric("TARGET 1",  f"{t1/total*100:.0f}%",      f"{t1}/{total}")
    am[3].metric("TARGET 2",  f"{t2/total*100:.0f}%",      f"{t2}/{total}")
    am[4].metric("SL HIT",    f"{sl/total*100:.0f}%",      f"{sl}/{total}")

    with st.expander("[LOG] Full outcome history"):
        df_log = pd.DataFrame(valid_log)
        cols   = ["date","symbol","signal","confidence",
                  "predicted_direction","actual_direction",
                  "was_correct","outcome","day_change_pct",
                  "sl_hit","target_1_reached","target_2_reached"]
        if "claude_grade" in df_log.columns:
            cols.append("claude_grade")
        st.dataframe(
            df_log[[c for c in cols if c in df_log.columns]].sort_values("date", ascending=False),
            use_container_width=True,
        )

# ---------------------------------------------------------------------------
# MORNING SUMMARY (footer)
# ---------------------------------------------------------------------------

summary = brief.get("morning_summary", "")
if summary:
    st.divider()
    st.markdown(
        f'<div style="background:{MIL["card"]};border:1px solid {MIL["border"]};'
        f'border-left:3px solid {MIL["green"]};padding:14px;'
        f'font-family:monospace;font-size:12px;color:{MIL["text2"]};line-height:1.6">'
        f'<span style="color:{MIL["green"]};font-weight:700;letter-spacing:1px">'
        f'[MORNING BRIEF] </span>{summary}</div>',
        unsafe_allow_html=True,
    )
