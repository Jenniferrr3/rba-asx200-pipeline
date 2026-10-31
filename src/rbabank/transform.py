"""TRANSFORM layer: align the two sources, calculate metrics, assign the RAG signal.

Reads only curated tables; writes only the serve table. Never touches raw.
Plain-language definitions of every metric are in docs/METRICS.md and were written
before this code.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


# ------------------------------------------------------------------ alignment
def align(asx: pd.DataFrame, rba: pd.DataFrame, calendar=None) -> pd.DataFrame:
    """One row = one ASX trading day (Sydney date). The ASX calendar is the spine.

    The cash rate is attached 'as of' that date: the latest RBA value dated on or
    before the trading day. If the RBA has no row for that day (holiday mismatch or
    late publication), the last known value is carried forward and flagged.
    Never look forward: a value dated after the trading day is never used.
    """
    a = asx.copy()
    r = rba.copy()
    a["trade_date"] = pd.to_datetime(a["trade_date"])
    if calendar is not None:
        # Keep quarantined trading days on the spine as blank rows, so a return is never
        # silently calculated across a missing day.
        cal = pd.DataFrame({"trade_date": pd.to_datetime(sorted(set(calendar)))})
        a = cal.merge(a, on="trade_date", how="left")
        a["price_quarantined"] = a["close"].isna()
    r["rate_date"] = pd.to_datetime(r["rate_date"])
    a, r = a.sort_values("trade_date"), r.sort_values("rate_date")
    m = pd.merge_asof(a, r, left_on="trade_date", right_on="rate_date", direction="backward")
    m["rate_carried_forward"] = m["rate_date"].notna() & (m["rate_date"] != m["trade_date"])
    m["rate_staleness_days"] = (m["trade_date"] - m["rate_date"]).dt.days
    m["rate_stale_flag"] = m["rate_staleness_days"] > config.MAX_RATE_STALENESS_DAYS
    return m


# ------------------------------------------------------------------ metrics
def add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    d = df.sort_values("trade_date").reset_index(drop=True).copy()

    # M1  20-day rolling average volume (blank volumes are skipped, need 15 of 20)
    d["volume_ma20"] = d["volume"].rolling(config.VOL_MA_WINDOW,
                                           min_periods=config.VOL_MA_MIN_OBS).mean()
    d["volume_vs_ma20_pct"] = (d["volume"] / d["volume_ma20"] - 1) * 100

    # M2  14-day realised volatility, annualised, in %
    #     daily log return between consecutive trading days; blank if either day is blank
    #     needs 12 valid returns in the 14-day window
    d["log_return"] = np.log(d["close"] / d["close"].shift(1))
    d["volatility_14d_ann_pct"] = (
        d["log_return"].rolling(config.VOLATILITY_WINDOW,
                                min_periods=config.VOLATILITY_MIN_OBS).std(ddof=1)
        * np.sqrt(config.TRADING_DAYS_PER_YEAR) * 100)

    # M3  20-day rate of change of the index close, in %
    d["roc_20d_pct"] = (d["close"] / d["close"].shift(config.ROC_WINDOW) - 1) * 100

    # Macro context: change in the cash rate target vs 20 trading days earlier, in bps
    d["cash_rate_change_20d_bps"] = (d["cash_rate_target"]
                                     - d["cash_rate_target"].shift(config.ROC_WINDOW)) * 100
    d["rate_changed_today"] = d["cash_rate_target"].diff().fillna(0).ne(0)
    return d


# ------------------------------------------------------------------ RAG signal
def add_rag(d: pd.DataFrame) -> pd.DataFrame:
    """RAG on 14-day volatility vs its own trailing year (252 trading days).

    The thresholds are percentiles of the PRIOR window (excluding today), so today's
    value is judged against what was normal before it, not against itself.
      GREEN  below the 75th percentile       -> within the normal range
      AMBER  75th to below 90th percentile   -> worth watching
      RED    90th percentile or above, OR amber 5 trading days in a row
      GREY   fewer than 120 prior observations -> not enough history to judge
    """
    d = d.copy()
    v = d["volatility_14d_ann_pct"]
    prior = v.shift(1).rolling(config.RAG_LOOKBACK, min_periods=config.RAG_MIN_HISTORY)
    d["vol_p75"] = prior.quantile(config.RAG_AMBER_PCTL)
    d["vol_p90"] = prior.quantile(config.RAG_RED_PCTL)
    # percentile rank of today's value within the prior window (for the headline sentence)
    vals = v.to_numpy()
    rank = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        w = vals[max(0, i - config.RAG_LOOKBACK):i]
        w = w[~np.isnan(w)]
        if len(w) >= config.RAG_MIN_HISTORY and not np.isnan(vals[i]):
            rank[i] = (w < vals[i]).mean() * 100
    d["vol_pctile_rank"] = rank

    rag = np.where(d["vol_p75"].isna() | v.isna(), "GREY",
                   np.where(v >= d["vol_p90"], "RED",
                            np.where(v >= d["vol_p75"], "AMBER", "GREEN")))
    # escalation: 5 consecutive amber days -> red from the 5th day on
    amber = pd.Series(rag == "AMBER")
    run = amber.groupby((~amber).cumsum()).cumsum()
    rag = np.where(amber & (run >= config.RAG_AMBER_ESCALATE_DAYS), "RED", rag)
    d["rag"] = rag
    d["rag_reason"] = np.select(
        [d["rag"].eq("GREY"),
         d["rag"].eq("RED") & amber & (run >= config.RAG_AMBER_ESCALATE_DAYS),
         d["rag"].eq("RED"), d["rag"].eq("AMBER"), d["rag"].eq("GREEN")],
        ["Not enough history (needs 120 prior trading days)",
         "Amber for 5+ trading days in a row",
         "Volatility at or above the 90th percentile of the past year",
         "Volatility between the 75th and 90th percentile of the past year",
         "Volatility below the 75th percentile of the past year"], default="")
    return d


SERVE_COLUMNS = [
    "trade_date", "close", "price_quarantined", "volume", "volume_flag", "volume_ma20", "volume_vs_ma20_pct",
    "log_return", "volatility_14d_ann_pct", "roc_20d_pct", "cash_rate_target",
    "interbank_overnight_rate", "rate_date", "rate_carried_forward", "rate_staleness_days",
    "rate_stale_flag", "cash_rate_change_20d_bps", "rate_changed_today", "vol_p75", "vol_p90",
    "vol_pctile_rank", "rag", "rag_reason",
]


def build_serve(asx_curated: pd.DataFrame, rba_curated: pd.DataFrame,
                calendar=None) -> pd.DataFrame:
    out = add_rag(add_metrics(align(asx_curated, rba_curated, calendar)))
    if "price_quarantined" not in out:
        out["price_quarantined"] = False
    out["trade_date"] = out["trade_date"].dt.date
    out["rate_date"] = out["rate_date"].dt.date
    return out[SERVE_COLUMNS]


# ------------------------------------------------------------------ how the 3 series relate
def _strength(r: float) -> str:
    a = abs(r)
    return "weak" if a < 0.3 else "moderate" if a < 0.6 else "strong"


def relationships(serve: pd.DataFrame, window_days: int = 90, event_days: int = 10) -> dict:
    """How the three time series move together. Descriptive only: it shows that they move
    together, not why.

      1. ASX 200 and volatility (last 90 days): correlation between the 20-day rate of change
         and 14-day volatility. Negative means the index tended to be falling when volatility
         was high.
      2. Cash rate and ASX 200 (whole history): for each change in the cash rate target, the
         index move over the 10 trading days from the day before the change.
      3. Cash rate and volatility (whole history): average volatility over the 10 trading days
         after each change, compared with the 10 days before it.
    """
    s = serve.copy()
    s["trade_date"] = pd.to_datetime(s["trade_date"])
    s = s.sort_values("trade_date").reset_index(drop=True)
    w = s[s["trade_date"] > s["trade_date"].max() - pd.Timedelta(days=window_days)]

    pair = w[["roc_20d_pct", "volatility_14d_ann_pct"]].dropna()
    r_iv = float(pair.corr().iloc[0, 1]) if len(pair) >= 20 else float("nan")

    events = []
    for i in s.index[s["rate_changed_today"].astype(bool)]:
        before, after = s.iloc[max(0, i - event_days):i], s.iloc[i:i + event_days]
        if len(before) < event_days or len(after) < event_days:
            continue
        base = s["close"].iloc[i - 1]
        end_close = after["close"].dropna()
        events.append({
            "date": s.at[i, "trade_date"].strftime("%Y-%m-%d"),
            "bps": round((s.at[i, "cash_rate_target"] - s.at[i - 1, "cash_rate_target"]) * 100),
            "index_move_pct": round((end_close.iloc[-1] / base - 1) * 100, 1),
            "vol_before": round(before["volatility_14d_ann_pct"].mean(), 1),
            "vol_after": round(after["volatility_14d_ann_pct"].mean(), 1),
        })
    ev = pd.DataFrame(events)
    return {
        "index_vs_volatility": {
            "r": None if np.isnan(r_iv) else round(r_iv, 2),
            "strength": None if np.isnan(r_iv) else _strength(r_iv),
            "days": int(len(pair))},
        "rate_events": events,
        "avg_vol_change_after_rate_move": None if ev.empty
        else round(float((ev["vol_after"] - ev["vol_before"]).mean()), 1),
    }
