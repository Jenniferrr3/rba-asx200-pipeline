"""SERVE -> dashboard. Reads only data/serve/. Writes one self-contained HTML page.

Audience: financially literate, non-technical managers. Question it answers:
"How has market activity trended over the past 90 days, and is there anything we
should be watching?"  The headline sentence is generated from the data so the
takeaway is stated, not left for the reader to find.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .transform import relationships

RAG_TEXT = {
    "GREEN": ("Normal", "Market volatility is within its usual range. No action needed."),
    "AMBER": ("Worth watching", "Volatility is higher than usual. Check the next two weeks "
              "of figures before drawing benchmark conclusions."),
    "RED": ("Needs attention", "Volatility is well above normal, or has stayed high. Treat "
            "short-term benchmark comparisons with caution and brief the desk."),
    "GREY": ("Not enough history", "Fewer than 120 trading days of history; the signal "
             "cannot be judged yet."),
}


def _num(x, nd=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def build_payload(serve: pd.DataFrame, status: dict) -> dict:
    s = serve.copy()
    s["trade_date"] = pd.to_datetime(s["trade_date"])
    end = s["trade_date"].max()
    w = s[s["trade_date"] > end - timedelta(days=config.DASHBOARD_DAYS)].copy()
    last, first = w.iloc[-1], w.iloc[0]

    chg_90 = (last["close"] / first["close"] - 1) * 100
    rate_chg_bps = (last["cash_rate_target"] - first["cash_rate_target"]) * 100
    # One definition of "the index move after a rate change", shared with the relationships
    # panel, so the same event never shows two different numbers on one page.
    rel = relationships(serve, config.DASHBOARD_DAYS)
    moves = {e["date"]: e["index_move_pct"] for e in rel["rate_events"]}
    changes = w[w["rate_changed_today"].astype(bool)]
    events = []
    for _, r in changes.iterrows():
        prev = s.loc[s["trade_date"] < r["trade_date"], "cash_rate_target"].iloc[-1]
        d = r["trade_date"].strftime("%Y-%m-%d")
        events.append({"date": d, "bps": round((r["cash_rate_target"] - prev) * 100),
                       "rate": _num(r["cash_rate_target"]), "index_move_10d_pct": moves.get(d)})

    rag = last["rag"]
    days_flagged = w["rag"].isin(["AMBER", "RED"]).sum()
    vol_ma_prev = w["volume_ma20"].dropna()
    vol_trend = (vol_ma_prev.iloc[-1] / vol_ma_prev.iloc[0] - 1) * 100 if len(vol_ma_prev) > 1 else None

    # ---- headline: one sentence, the "so what"
    direction = "rose" if chg_90 >= 0 else "fell"
    # Say what happened to each series side by side. Do not imply the rate move caused the
    # index move: a 90-day chart cannot show that.
    if events:
        e = events[-1]
        rate_part = (f"The RBA {'raised' if e['bps'] > 0 else 'cut'} the cash rate "
                     f"{abs(e['bps'])} bp to {e['rate']:.2f}% on {pd.Timestamp(e['date']):%d %b}")
    else:
        rate_part = f"The cash rate held at {last['cash_rate_target']:.2f}%"
    vol_part = {"GREEN": "volatility is within its normal range",
                "AMBER": "volatility is above its normal range and worth watching",
                "RED": "volatility is well above normal and needs attention",
                "GREY": "there is not yet enough history to judge volatility"}[rag]
    headline = (f"The ASX 200 {direction} {abs(chg_90):.1f}% over the past 90 days. "
                f"{rate_part}, and {vol_part}.")

    vq = int(w["volume_flag"].fillna("").ne("").sum())
    stale = [k for k, v in status.get("sources", {}).items() if v.get("stale")]
    payload = {
        "headline": headline,
        "period": f"{first['trade_date']:%d %b %Y} – {last['trade_date']:%d %b %Y}",
        "rag": rag, "rag_label": RAG_TEXT[rag][0], "rag_action": RAG_TEXT[rag][1],
        "rag_reason": last["rag_reason"],
        "vol_now": _num(last["volatility_14d_ann_pct"], 1),
        "vol_rank": _num(last["vol_pctile_rank"], 0),
        "p75": _num(last["vol_p75"], 1), "p90": _num(last["vol_p90"], 1),
        "days_flagged": int(days_flagged), "days_in_window": int(len(w)),
        "close": _num(last["close"], 0), "chg_90": _num(chg_90, 1),
        "roc20": _num(last["roc_20d_pct"], 1),
        "vol_trend": _num(vol_trend, 0),
        "rate": _num(last["cash_rate_target"]), "rate_chg_bps": _num(rate_chg_bps, 0),
        "events": events,
        "relations": rel,
        "health": {
            "mode": status.get("mode"), "run_id": status.get("run_id"),
            "sources": {k: {"stale": v.get("stale"), "last_success_utc": v.get("last_success_utc"),
                            "rows_quarantined": v.get("rows_quarantined"),
                            "reason": v.get("reason")} for k, v in status.get("sources", {}).items()},
            "stale_sources": stale, "volume_days_blanked": vq,
            "carried_forward_days": int(w["rate_carried_forward"].astype(bool).sum()),
        },
        "series": {
            "date": w["trade_date"].dt.strftime("%Y-%m-%d").tolist(),
            "close": [_num(x, 1) for x in w["close"]],
            "volume_m": [_num(x / 1e6, 1) if pd.notna(x) else None for x in w["volume"]],
            "volume_ma20_m": [_num(x / 1e6, 1) if pd.notna(x) else None for x in w["volume_ma20"]],
            "rate": [_num(x) for x in w["cash_rate_target"]],
            "vol14": [_num(x, 2) for x in w["volatility_14d_ann_pct"]],
            "p75": [_num(x, 2) for x in w["vol_p75"]], "p90": [_num(x, 2) for x in w["vol_p90"]],
            "rag": w["rag"].tolist(),
        },
    }
    return payload


TEMPLATE = Path(__file__).with_name("dashboard_template.html")


def render(payload: dict, illustrative: bool, standalone: bool = True) -> str:
    html = TEMPLATE.read_text()
    banner = ('<div class="banner">Illustrative sample data for design review – not real market '
              'figures. Run the notebook to rebuild this page from live RBA and Yahoo data.</div>'
              if illustrative else "")
    html = html.replace("/*__DATA__*/null", json.dumps(payload)).replace("<!--__BANNER__-->", banner)
    if standalone:  # a normal web page you can open from Colab, GitHub Pages or a laptop
        html = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<style>body{margin:0}</style></head><body>\n' + html + '\n</body></html>\n')
    return html


def write_dashboard(out: Path | None = None, standalone: bool = True) -> Path:
    serve = pd.read_csv(config.SERVE / "dashboard_daily.csv")
    status = json.loads((config.SERVE / "run_status.json").read_text())
    out = out or (config.ROOT / "docs" / "dashboard.html")
    out.write_text(render(build_payload(serve, status), illustrative=status.get("mode") == "offline",
                          standalone=standalone))
    return out
