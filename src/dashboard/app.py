"""
AI Trade Dashboard — Streamlit web app.
Reads the morning trade brief JSON and displays a live trading dashboard.
Auto-refreshes every 60 seconds to pick up the 9 AM pre-open update.
"""

import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz
import streamlit as st

# ---------------------------------------------------------------------------
# Config & paths
# ---------------------------------------------------------------------------

st.set_page_config(
    layout="wide",
    page_title="AI Trade Dashboard",
    page_icon="📈",
    initial_sidebar_state="collapsed",
)

ROOT      = Path(__file__).parent.parent.parent
DATA_DIR  = ROOT / "data" / "processed"
IST       = pytz.timezone("Asia/Kolkata")
TODAY_STR = datetime.now(IST).strftime("%Y-%m-%d")

# ---------------------------------------------------------------------------
# Color palettes
# ---------------------------------------------------------------------------

BIAS_COLOR  = {"BULLISH": "#00c853", "BEARISH": "#d50000", "MIXED": "#ff6d00"}
VIX_COLOR   = {"LOW": "#00c853", "NORMAL": "#4caf50", "ELEVATED": "#ff9800",
               "HIGH": "#f44336", "CRISIS": "#b71c1c"}
RISK_COLOR  = {"LOW": "#00c853", "MEDIUM": "#ff9800", "HIGH": "#f44336",
               "VERY_HIGH": "#b71c1c"}
SIG_COLOR   = {"CALL": "#00c853", "PUT": "#d50000", "NEUTRAL": "#607d8b"}
ACTION_COLOR = {"GO": "#00c853", "WAIT": "#ff9800", "SKIP": "#d50000"}

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _badge(text: str, bg: str = "#555", size: int = 13) -> str:
    return (
        f'<span style="background:{bg};color:#fff;padding:3px 11px;'
        f'border-radius:10px;font-weight:700;font-size:{size}px;'
        f'margin-right:6px;white-space:nowrap">{text}</span>'
    )

def _pill(text: str, color: str = "#555") -> str:
    return (
        f'<span style="background:{color}22;color:{color};'
        f'border:1px solid {color}55;padding:2px 10px;border-radius:12px;'
        f'font-size:13px;margin:2px 3px;display:inline-block">{text}</span>'
    )

def _conf_bar(label: str, value: int | float, max_val: int = 10) -> str:
    pct   = min(value / max_val * 100, 100)
    color = "#d50000" if value < 5 else "#ff9800" if value < 7 else "#00c853"
    short = {
        "global_cues":        "Global Cues",
        "institutional_flow": "Institutional",
        "derivative_signals": "Derivatives",
        "technical_analysis": "Technicals",
        "volume_confirmation":"Volume",
        "news_sentiment":     "News",
        "event_risk":         "Event Risk",
        "risk_reward_quality":"Risk:Reward",
    }.get(label, label)
    return (
        f'<div style="margin:3px 0">'
        f'<div style="display:flex;justify-content:space-between;'
        f'font-size:11px;color:#bbb"><span>{short}</span><span>{value}/10</span></div>'
        f'<div style="background:#2a2a2a;border-radius:3px;height:6px">'
        f'<div style="background:{color};width:{pct}%;height:6px;border-radius:3px"></div>'
        f'</div></div>'
    )

def _cue_card(label: str, last: float, pct: float, invert: bool = False) -> str:
    arrow = "▲" if pct >= 0 else "▼"
    color = "#00c853" if pct >= 0 else "#d50000"
    if invert:
        color = "#d50000" if pct >= 0 else "#00c853"
    return (
        f'<div style="background:#1a1a2e;border:1px solid #333;padding:10px 8px;'
        f'border-radius:8px;text-align:center;height:80px">'
        f'<div style="font-size:10px;color:#888;margin-bottom:3px">{label}</div>'
        f'<div style="font-size:16px;font-weight:700">{last:,.2f}</div>'
        f'<div style="font-size:13px;color:{color};font-weight:600">'
        f'{arrow} {pct:+.2f}%</div></div>'
    )

def _level_chip(val: int | float, kind: str) -> str:
    color = "#00c853" if kind == "support" else "#d50000"
    return _pill(f"{val:,.0f}", color)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

morning_path  = DATA_DIR / f"trade_brief_morning_{TODAY_STR}.json"
preopen_path  = DATA_DIR / f"trade_brief_preopen_{TODAY_STR}.json"
accuracy_path = DATA_DIR / "prediction_accuracy.json"

if not morning_path.exists():
    st.error(
        f"No morning brief found for {TODAY_STR}. "
        f"Run `python main.py` to generate the analysis."
    )
    st.info("Retrying in 30 seconds...")
    time.sleep(30)
    st.rerun()

brief    = json.loads(morning_path.read_text(encoding="utf-8"))
preopen  = (json.loads(preopen_path.read_text(encoding="utf-8"))
            if preopen_path.exists() else None)
acc_log  = (json.loads(accuracy_path.read_text(encoding="utf-8"))
            if accuracy_path.exists() else [])

ctx   = brief.get("market_context", {})
meta  = brief.get("_meta", {})
cues  = meta.get("global_cues", {})

# ---------------------------------------------------------------------------
# Auto-refresh via HTML meta tag — doesn't block interactions
# ---------------------------------------------------------------------------

st.markdown('<meta http-equiv="refresh" content="60">', unsafe_allow_html=True)

# Dark theme CSS
st.markdown("""
<style>
  .main { background:#0e0e1a; }
  .block-container { padding-top:1rem; }
  h1 { font-size:1.6rem !important; }
  h2 { font-size:1.2rem !important; border-bottom:1px solid #333; padding-bottom:4px; }
  .stMetric label { font-size:11px !important; color:#888 !important; }
  .stMetric [data-testid="stMetricValue"] { font-size:20px !important; font-weight:700; }
  .stAlert { border-radius:8px; }
  div[data-testid="stExpander"] { border:1px solid #333 !important; border-radius:8px; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# HEADER
# ---------------------------------------------------------------------------

analysis_time = brief.get("analysis_time", "08:00 IST")
date_raw      = brief.get("date", TODAY_STR)
try:
    display_date = datetime.strptime(date_raw, "%Y-%m-%d").strftime("%A, %d %b %Y")
except Exception:
    display_date = date_raw

st.title(f"📈  AI Trade Dashboard  —  {display_date}")
st.caption(f"Analysis at {analysis_time}  ·  Auto-refreshes every 60 s  ·  Last load: {datetime.now(IST).strftime('%H:%M:%S IST')}")

bias   = ctx.get("overall_market_bias", "MIXED")
vzone  = ctx.get("vix_zone", "NORMAL")
risk   = ctx.get("overall_risk_rating", "MEDIUM")
t_ok   = ctx.get("trading_recommended", True)

header_badges = (
    _badge(bias,  BIAS_COLOR.get(bias, "#555"), 14) +
    _badge(f"VIX {vzone}", VIX_COLOR.get(vzone, "#555"), 14) +
    _badge(risk,  RISK_COLOR.get(risk, "#555"), 14) +
    _badge("TRADING: YES" if t_ok else "TRADING: SKIP",
           "#00c853" if t_ok else "#d50000", 14)
)
st.markdown(header_badges, unsafe_allow_html=True)

if ctx.get("trading_caution"):
    st.markdown(
        f'<div style="background:#2a1a00;border-left:4px solid #ff9800;'
        f'padding:8px 14px;border-radius:4px;font-size:13px;margin:8px 0">'
        f'⚠️ {ctx["trading_caution"]}</div>',
        unsafe_allow_html=True,
    )

st.divider()

# ---------------------------------------------------------------------------
# MARKET METRICS
# ---------------------------------------------------------------------------

st.subheader("Market Snapshot")

vix_val    = meta.get("vix_at_8am", "—")
n_spot     = meta.get("nifty_spot", "—")
n_pcr      = meta.get("nifty_pcr", "—")
bn_spot    = meta.get("banknifty_spot", "—")
g_score    = ctx.get("global_score", "—")
crude_data = cues.get("Crude Oil WTI", {})
crude_val  = crude_data.get("last")
crude_pct  = crude_data.get("pct_change", 0) or 0
fii        = meta.get("fii_dii", {})
fii_net    = fii.get("fii_net_buy")

def _fmt(v, fmt="{:.2f}"):
    return fmt.format(v) if isinstance(v, (int, float)) else str(v)

c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
c1.metric("India VIX",       _fmt(vix_val))
c2.metric("Nifty Spot",      f"{n_spot:,.1f}"  if isinstance(n_spot,  (int,float)) else str(n_spot))
c3.metric("Nifty PCR",       _fmt(n_pcr))
c4.metric("BankNifty Spot",  f"{bn_spot:,.1f}" if isinstance(bn_spot, (int,float)) else str(bn_spot))
c5.metric("Crude WTI",
          f"${crude_val:.2f}" if isinstance(crude_val, (int,float)) else "—",
          delta=f"{crude_pct:+.2f}%" if crude_pct else None,
          delta_color="inverse")
c6.metric("Global Score",    f"{g_score} / +3" if isinstance(g_score, (int,float)) else str(g_score))
c7.metric("FII Net (cr)",
          f"₹{fii_net:+,.0f}" if isinstance(fii_net, (int,float)) else "—",
          delta_color="normal" if isinstance(fii_net, (int,float)) and fii_net >= 0 else "inverse")

# ---------------------------------------------------------------------------
# GLOBAL CUES
# ---------------------------------------------------------------------------

INVERT_LABELS = {"Crude Oil WTI", "USD/INR"}

if cues:
    st.subheader("Global Cues")
    cue_list = [(lbl, d) for lbl, d in cues.items()
                if isinstance(d, dict) and d.get("last") is not None]
    n_cols = min(len(cue_list), 5)
    if n_cols:
        cols = st.columns(n_cols)
        for i, (lbl, d) in enumerate(cue_list):
            pct  = d.get("pct_change") or 0
            last = d.get("last", 0)
            cols[i % n_cols].markdown(
                _cue_card(lbl, last, pct, invert=lbl in INVERT_LABELS),
                unsafe_allow_html=True,
            )

st.divider()

# ---------------------------------------------------------------------------
# WARNINGS
# ---------------------------------------------------------------------------

warnings = brief.get("event_warnings", [])
if warnings:
    st.subheader("Event Warnings")
    for w in warnings:
        st.warning(w, icon="⚠️")

# Check for expiry day warning across trades
if any(t.get("expiry_day_warning") for t in brief.get("trades", [])):
    st.error("🔴 EXPIRY DAY WARNING: One or more recommended strikes expire TODAY. "
             "Do NOT hold past 1 PM IST. Exit or roll before theta decay accelerates.", icon="🚨")

# ---------------------------------------------------------------------------
# ACTIVE TRADES
# ---------------------------------------------------------------------------

st.subheader("Active Trade Recommendations")

trades = brief.get("trades", [])
if not trades:
    st.info("No trades met the confidence threshold (≥ 7.0) today.")
else:
    for t in trades:
        sig    = t.get("signal", "?")
        sym    = t.get("symbol", "?")
        conf   = t.get("confidence", 0)
        sig_c  = SIG_COLOR.get(sig, "#607d8b")
        conf_c = "#00c853" if conf >= 8 else "#ff9800" if conf >= 7 else "#d50000"
        size   = t.get("position_size_recommendation", "HALF")
        size_c = "#00c853" if size == "FULL" else "#ff9800" if size == "HALF" else "#f44336"

        expiry_warn = t.get("expiry_day_warning", False)
        rec_risk    = t.get("mid_session_recovery_risk", "")
        rec_high    = "HIGH" in str(rec_risk).upper()

        with st.container():
            st.markdown(
                f'<div style="background:#12122a;border:1px solid {sig_c}55;'
                f'border-left:4px solid {sig_c};border-radius:8px;padding:16px;margin:8px 0">',
                unsafe_allow_html=True,
            )

            # Trade header row
            hcol1, hcol2, hcol3 = st.columns([3, 2, 1])
            with hcol1:
                st.markdown(
                    f'<div style="font-size:22px;font-weight:800">{sym}</div>'
                    + _badge(sig, sig_c, 15)
                    + _badge(f"Strike {t.get('strike','?')}", "#334", 13)
                    + _badge(t.get("expiry","?"), "#334" if not expiry_warn else "#8b0000", 13)
                    + (" " + _badge("EXPIRY DAY", "#d50000", 13) if expiry_warn else ""),
                    unsafe_allow_html=True,
                )
            with hcol2:
                st.markdown(
                    f'<div style="font-size:13px;color:#888;margin-bottom:4px">Position Size</div>'
                    + _badge(size, size_c, 15),
                    unsafe_allow_html=True,
                )
            with hcol3:
                st.markdown(
                    f'<div style="text-align:right">'
                    f'<div style="font-size:11px;color:#888">Confidence</div>'
                    f'<div style="font-size:32px;font-weight:900;color:{conf_c}">{conf}</div>'
                    f'<div style="font-size:11px;color:#888">/ 10</div></div>',
                    unsafe_allow_html=True,
                )

            st.markdown("<br>", unsafe_allow_html=True)

            # Price levels row
            lc1, lc2, lc3, lc4, lc5 = st.columns(5)
            def _price_box(label, val, bg):
                return (f'<div style="background:{bg}22;border:1px solid {bg}66;'
                        f'border-radius:6px;padding:8px;text-align:center">'
                        f'<div style="font-size:10px;color:#aaa">{label}</div>'
                        f'<div style="font-size:18px;font-weight:700;color:{bg}">{val}</div></div>')

            lc1.markdown(_price_box("Entry",    t.get("entry_price","?"), "#ffffff"), unsafe_allow_html=True)
            lc2.markdown(_price_box("Stop Loss", t.get("stop_loss","?"),  "#f44336"), unsafe_allow_html=True)
            lc3.markdown(_price_box("Target 1",  t.get("target_1","?"),   "#66bb6a"), unsafe_allow_html=True)
            lc4.markdown(_price_box("Target 2",  t.get("target_2","?"),   "#00c853"), unsafe_allow_html=True)
            lc5.markdown(_price_box("R : R",     f"1:{t.get('risk_reward','?')}", "#42a5f5"), unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # Confidence breakdown + details side by side
            dcol1, dcol2 = st.columns([1, 1])
            with dcol1:
                st.markdown("**Confidence Breakdown**")
                bd = t.get("confidence_breakdown", {})
                bars_html = "".join(_conf_bar(k, v) for k, v in bd.items())
                st.markdown(f'<div style="padding:4px 0">{bars_html}</div>',
                            unsafe_allow_html=True)

            with dcol2:
                # Entry trigger
                trigger = t.get("entry_trigger", "")
                if trigger:
                    st.markdown(
                        f'<div style="background:#1a1200;border-left:3px solid #ff9800;'
                        f'padding:8px 12px;border-radius:4px;font-size:12px;margin:4px 0">'
                        f'🟠 <b>Entry Trigger:</b><br>{trigger}</div>',
                        unsafe_allow_html=True,
                    )
                # Mid-session recovery risk
                if rec_risk:
                    rc = "#d50000" if rec_high else "#4caf50"
                    bg = "#1a0000" if rec_high else "#001a00"
                    st.markdown(
                        f'<div style="background:{bg};border-left:3px solid {rc};'
                        f'padding:8px 12px;border-radius:4px;font-size:12px;margin:4px 0">'
                        f'{"🔴" if rec_high else "🟢"} <b>Recovery Risk:</b><br>{rec_risk}</div>',
                        unsafe_allow_html=True,
                    )
                # Key risk
                key_risk = t.get("key_risk", "")
                if key_risk:
                    st.markdown(
                        f'<div style="background:#1a0000;border-left:3px solid #d50000;'
                        f'padding:8px 12px;border-radius:4px;font-size:12px;margin:4px 0">'
                        f'🔴 <b>Key Risk:</b><br>{key_risk}</div>',
                        unsafe_allow_html=True,
                    )

            # Reasoning (expandable)
            reasoning = t.get("reasoning", "")
            if reasoning:
                with st.expander("Reasoning"):
                    st.markdown(f'<div style="font-size:13px;color:#ccc">{reasoning}</div>',
                                unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)

st.divider()

# ---------------------------------------------------------------------------
# FILTERED TRADES
# ---------------------------------------------------------------------------

filtered = brief.get("trades_filtered_out", [])
if filtered:
    with st.expander(f"Skipped Trades — confidence below 7.0  ({len(filtered)} trade{'s' if len(filtered)>1 else ''})"):
        for ft in filtered:
            sig_c = SIG_COLOR.get(ft.get("signal",""), "#607d8b")
            st.markdown(
                _badge(ft.get("symbol","?"), "#333", 13) +
                _badge(ft.get("signal","?"), sig_c, 13) +
                _badge(f"conf: {ft.get('confidence','?')}", "#555", 13) +
                f'  <span style="font-size:12px;color:#aaa">{ft.get("filter_reason","")}</span>',
                unsafe_allow_html=True,
            )
            st.markdown('<hr style="border-color:#222;margin:6px 0">', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# KEY LEVELS
# ---------------------------------------------------------------------------

st.subheader("Key Support & Resistance Levels")
levels = brief.get("key_levels", {})

kl1, kl2 = st.columns(2)
with kl1:
    st.markdown("**NIFTY**")
    sup_html = "".join(_level_chip(v, "support")    for v in levels.get("nifty_support", []))
    res_html = "".join(_level_chip(v, "resistance") for v in levels.get("nifty_resistance", []))
    st.markdown(
        f'Support &nbsp; {sup_html}<br>Resistance {res_html}',
        unsafe_allow_html=True,
    )

with kl2:
    st.markdown("**BANKNIFTY**")
    sup_html = "".join(_level_chip(v, "support")    for v in levels.get("banknifty_support", []))
    res_html = "".join(_level_chip(v, "resistance") for v in levels.get("banknifty_resistance", []))
    st.markdown(
        f'Support &nbsp; {sup_html}<br>Resistance {res_html}',
        unsafe_allow_html=True,
    )

st.divider()

# ---------------------------------------------------------------------------
# SECTORS
# ---------------------------------------------------------------------------

avoid  = brief.get("sectors_to_avoid_today", [])
favor  = brief.get("sectors_to_favor_today", [])
watch  = brief.get("stocks_to_watch", [])

if avoid or favor:
    st.subheader("Sector Rotation")
    sc1, sc2 = st.columns(2)
    with sc1:
        st.markdown("**Avoid Today 🔴**")
        if avoid:
            st.markdown(" ".join(_pill(s[:50], "#d50000") for s in avoid),
                        unsafe_allow_html=True)
        else:
            st.caption("None flagged")
    with sc2:
        st.markdown("**Favor Today 🟢**")
        if favor:
            st.markdown(" ".join(_pill(s[:50], "#00c853") for s in favor),
                        unsafe_allow_html=True)
        else:
            st.caption("None flagged")

if watch:
    with st.expander("Stocks to Watch"):
        for s in watch:
            st.markdown(f"• {s}")

st.divider()

# ---------------------------------------------------------------------------
# ACCURACY TRACKER
# ---------------------------------------------------------------------------

st.subheader("Prediction Accuracy Tracker")

valid_log = [r for r in acc_log if not r.get("error")]
if not valid_log:
    st.info("No tracked outcomes yet. Runs automatically at 3:30 PM IST each trading day.")
else:
    total    = len(valid_log)
    correct  = sum(1 for r in valid_log if r.get("was_correct"))
    t1_hits  = sum(1 for r in valid_log if r.get("target_1_reached"))
    t2_hits  = sum(1 for r in valid_log if r.get("target_2_reached"))
    sl_hits  = sum(1 for r in valid_log if r.get("sl_hit"))

    am1, am2, am3, am4, am5 = st.columns(5)
    am1.metric("Trades Tracked",  total)
    am2.metric("Direction ✓",     f"{correct/total*100:.0f}%",  f"{correct}/{total}")
    am3.metric("Target 1 Hit",    f"{t1_hits/total*100:.0f}%",  f"{t1_hits}/{total}")
    am4.metric("Target 2 Hit",    f"{t2_hits/total*100:.0f}%",  f"{t2_hits}/{total}")
    am5.metric("SL Hit",          f"{sl_hits/total*100:.0f}%",  f"{sl_hits}/{total}")

    # Bar chart — last 7 days: correct vs wrong per date
    df_log = pd.DataFrame(valid_log)
    df_log["correct_int"] = df_log["was_correct"].astype(int)
    df_log["wrong_int"]   = (~df_log["was_correct"]).astype(int)

    chart_df = (df_log.groupby("date")[["correct_int","wrong_int"]]
                .sum().tail(7)
                .rename(columns={"correct_int":"Correct", "wrong_int":"Wrong"}))

    if not chart_df.empty:
        st.markdown("**Last 7 days — direction accuracy**")
        st.bar_chart(chart_df, color=["#00c853", "#d50000"])

    # Detail table
    with st.expander("Full outcome log"):
        display_cols = ["date","symbol","signal","confidence",
                        "predicted_direction","actual_direction",
                        "was_correct","outcome","day_change_pct",
                        "sl_hit","target_1_reached","target_2_reached"]
        st.dataframe(
            df_log[[c for c in display_cols if c in df_log.columns]]
            .sort_values("date", ascending=False),
            use_container_width=True,
        )

st.divider()

# ---------------------------------------------------------------------------
# 9 AM PRE-OPEN UPDATE
# ---------------------------------------------------------------------------

if preopen:
    st.subheader("9:00 AM Pre-Open Final Check")

    po_vix    = preopen.get("vix_current", "—")
    po_global = preopen.get("global_bias_current", "—")
    po_open   = preopen.get("market_opening_outlook", "—")

    pc1, pc2, pc3 = st.columns(3)
    pc1.metric("VIX (fresh)",    po_vix)
    pc2.metric("Global Bias",    po_global)
    pc3.metric("Opening Outlook",po_open)

    for tr in preopen.get("trades_review", []):
        action = tr.get("final_action", "?")
        ac     = ACTION_COLOR.get(action, "#555")
        sym    = tr.get("symbol", "?")
        sig    = tr.get("signal", "?")

        st.markdown(
            f'<div style="background:#12122a;border:1px solid {ac}55;'
            f'border-left:5px solid {ac};border-radius:8px;'
            f'padding:12px 16px;margin:6px 0;display:flex;align-items:center;gap:16px">'
            f'<div style="font-size:28px;font-weight:900;color:{ac};min-width:60px">{action}</div>'
            f'<div>'
            f'<div style="font-weight:700;font-size:16px">{sym} {sig} {tr.get("strike","")}</div>'
            f'<div style="font-size:13px;color:#ccc">{tr.get("final_action_reason","")}</div>'
            + (f'<div style="font-size:12px;color:#aaa;margin-top:4px">Note: {tr["note"]}</div>'
               if tr.get("note") else "")
            + (f'<div style="font-size:12px;color:#ff9800;margin-top:4px">Adjusted entry: {tr["adjusted_entry"]}</div>'
               if tr.get("adjusted_entry") else "")
            + f'</div></div>',
            unsafe_allow_html=True,
        )

    if preopen.get("preopen_summary"):
        st.info(preopen["preopen_summary"])
else:
    st.info(
        "9 AM pre-open update not yet available. "
        "It appears here automatically after `python main.py --preopen` runs at 9:00 AM IST."
    )

# ---------------------------------------------------------------------------
# MORNING SUMMARY (footer)
# ---------------------------------------------------------------------------

summary = brief.get("morning_summary", "")
if summary:
    st.divider()
    st.markdown(
        f'<div style="background:#0a1628;border:1px solid #1a3a5c;'
        f'border-radius:8px;padding:16px;font-size:14px;color:#ccc;line-height:1.6">'
        f'<b style="color:#42a5f5">Morning Summary</b><br><br>{summary}</div>',
        unsafe_allow_html=True,
    )
