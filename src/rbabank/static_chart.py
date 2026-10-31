"""A static PNG of the three dashboard panels, for the written report and slides.
Same serve table, same numbers as the interactive page.

    PYTHONPATH=src python -m rbabank.static_chart
"""
from __future__ import annotations

from datetime import timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from . import config

INK, MUTED, LINE = "#17212b", "#5b6875", "#dde2e7"
ACCENT, SOFT, RATE = "#0f5c6e", "#bcd9e0", "#a8551b"
AMBER, RED = "#f5d9a0", "#f3c2be"
RAG_COLOUR = {"GREEN": "#1d7a46", "AMBER": "#a86a00", "RED": "#b3261e", "GREY": "#6b7580"}


def draw(out=None, illustrative=True):
    s = pd.read_csv(config.SERVE / "dashboard_daily.csv", parse_dates=["trade_date"])
    w = s[s["trade_date"] > s["trade_date"].max() - timedelta(days=config.DASHBOARD_DAYS)]
    last = w.iloc[-1]

    plt.rcParams.update({"font.size": 9, "axes.edgecolor": LINE, "axes.labelcolor": MUTED,
                         "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False})
    fig, ax = plt.subplots(3, 1, figsize=(10, 9), sharex=True,
                           gridspec_kw={"height_ratios": [1.2, 1, 1]})
    title = f"ASX 200, last 90 days: signal {last['rag']}"
    if illustrative:
        title += "  (illustrative sample data)"
    fig.suptitle(title, x=0.06, ha="left", fontsize=13, color=INK, fontweight="bold")

    a = ax[0]
    a.plot(w["trade_date"], w["close"], color=ACCENT, lw=2)
    a.set_ylabel("ASX 200 close")
    a.spines["right"].set_visible(True)
    b = a.twinx()
    b.step(w["trade_date"], w["cash_rate_target"], where="post", color=RATE, lw=2)
    b.set_ylabel("Cash rate target (%)", color=RATE)
    b.set_ylim(w["cash_rate_target"].min() - 0.5, w["cash_rate_target"].max() + 0.5)
    for d in w.loc[w["rate_changed_today"].astype(bool), "trade_date"]:
        a.axvline(d, color=RATE, ls=":", lw=1)
        a.annotate("RBA rate change", (d, a.get_ylim()[1]), xytext=(4, -12),
                   textcoords="offset points", color=RATE, fontsize=8)
    a.set_title("Index level against the cash rate", loc="left", color=INK)

    a = ax[1]
    a.bar(w["trade_date"], w["volume"] / 1e6, color=SOFT, width=0.8)
    a.plot(w["trade_date"], w["volume_ma20"] / 1e6, color=ACCENT, lw=2)
    a.set_ylabel("Volume (m)")
    a.set_title("Daily volume and its 20-day average", loc="left", color=INK)

    a = ax[2]
    top = max(w["volatility_14d_ann_pct"].max(), w["vol_p90"].max()) * 1.25
    a.fill_between(w["trade_date"], w["vol_p75"], w["vol_p90"], color=AMBER, lw=0, label="Amber zone")
    a.fill_between(w["trade_date"], w["vol_p90"], top, color=RED, lw=0, alpha=.7, label="Red zone")
    a.plot(w["trade_date"], w["volatility_14d_ann_pct"], color=INK, lw=2, label="14-day volatility")
    a.scatter([last["trade_date"]], [last["volatility_14d_ann_pct"]], s=40, zorder=3,
              color=RAG_COLOUR[last["rag"]])
    a.set_ylim(0, top)
    a.set_ylabel("Annualised %")
    a.legend(loc="upper left", frameon=False, ncol=3, fontsize=8)
    a.set_title("Volatility signal (zones = 75th and 90th percentile of the prior year)",
                loc="left", color=INK)
    a.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    for x in ax:
        x.grid(axis="y", color=LINE, lw=.6)
    fig.tight_layout()
    out = out or config.ROOT / "docs" / "dashboard_static.png"
    fig.savefig(out, dpi=160)
    return out


if __name__ == "__main__":
    print(draw())
