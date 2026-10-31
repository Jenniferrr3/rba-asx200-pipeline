"""Generates RBA_ASX200_PoC.ipynb (kept as code so the notebook diff is reviewable)."""
import json
from pathlib import Path

REPO = "https://github.com/YOUR-GITHUB-USERNAME/rba-asx200-pipeline.git"

cells = [
("md", """# RBA cash rate + S&P/ASX 200: proof of concept
Really Big Bank · post-trade analytics · BUSA3022 case study

This notebook runs the whole thin slice on **live data**:

1. Tests (hand-worked metric checks, time zone, failure handling)\n   *(Model choice and the prompts used: docs/PROMPTS.md)*
2. Live ingest: RBA Table F1 cash rate target + Yahoo Finance ^AXJO → raw → curated → serve
3. Data-quality evidence: rows in = rows passed + rows quarantined
4. One metric checked by hand against the pipeline's number
5. Two failure scenarios (API timeout, schema change) run in a separate copy so live data is untouched
6. The 90-day dashboard with the RAG signal

**Before running:** replace `YOUR-GITHUB-USERNAME` in the next cell, then *Runtime → Run all*."""),
("code", f"""REPO = "{REPO}"
!rm -rf /content/rba-asx200-pipeline
!git clone -q $REPO /content/rba-asx200-pipeline
%cd /content/rba-asx200-pipeline
!pip -q install -r requirements.txt
import sys; sys.path.insert(0, "src")"""),
("md", "## 1. Tests\nEvery metric is checked against numbers worked out by hand. If this fails, stop: nothing downstream can be trusted."),
("code", "!PYTHONPATH=src pytest -q"),
("md", "## 2. Live run: ingest → raw → validate → curated → transform → serve"),
("code", """from rbabank import pipeline, config
import json, pandas as pd
pd.set_option("display.width", 200); pd.set_option("display.max_columns", 30)
pipeline.reset_data()                      # start clean for the demo
status = pipeline.run(offline=False)
print(json.dumps(status, indent=2, default=str))"""),
("md", "### Raw layer: files exactly as received (never edited)"),
("code", """!find data/raw -type f | head
rba_raw = sorted(config.RAW.glob("rba/*/*.csv"))[-1]
print(rba_raw.read_bytes()[:700].decode("cp1252", "replace"))"""),
("md", "## 3. Data quality: nothing silently dropped"),
("code", """dq = pd.read_csv(config.QUALITY / "dq_log.csv"); display(dq)
q = config.QUALITY / "quarantine.csv"
display(pd.read_csv(q) if q.exists() else "No rows quarantined this run")
for src, s in status["sources"].items():
    if s.get("loaded"):
        print(f"{src}: {s['rows_passed']} passed + {s['rows_quarantined']} quarantined")"""),
("md", """### Anticipated issue: zero or missing index volume on Yahoo
Index volume from Yahoo can be 0 or blank on some days. A zero would drag the 20-day average down, so it is set to blank and flagged. Check how common it is in the live data:"""),
("code", """asx = pd.read_csv(config.CURATED / "asx200_daily.csv")
share = (asx["volume_flag"].fillna("") != "").mean()
print(f"{(asx['volume_flag'].fillna('') != '').sum()} of {len(asx)} days had zero/missing volume ({share:.1%})")
display(asx[asx["volume_flag"].fillna("") != ""].tail())"""),
("md", "## 4. Curated + aligned data, and one metric checked by hand"),
("code", """serve = pd.read_csv(config.SERVE / "dashboard_daily.csv")
display(serve.tail(10)[["trade_date","close","volume","volume_ma20","volatility_14d_ann_pct",
                         "roc_20d_pct","cash_rate_target","rate_carried_forward","rag"]].round(2))
print("Trading days that used a carried-forward RBA value:")
display(serve[serve.rate_carried_forward][["trade_date","rate_date","cash_rate_target"]].tail())"""),
("code", """# Hand check of 14-day volatility for the latest day, written out step by step
import math
last15 = serve["close"].tail(15).tolist()
r = [math.log(last15[i] / last15[i-1]) for i in range(1, 15)]
mean = sum(r) / len(r)
sd = math.sqrt(sum((x - mean) ** 2 for x in r) / (len(r) - 1))
by_hand = sd * math.sqrt(252) * 100
pipe = serve["volatility_14d_ann_pct"].iloc[-1]
print(f"by hand: {by_hand:.4f}%   pipeline: {pipe:.4f}%   match: {abs(by_hand - pipe) < 1e-9}")"""),
("md", "### How often did the RAG signal fire in the last 90 days? (threshold justification)"),
("code", """s = serve.copy(); s["trade_date"] = pd.to_datetime(s["trade_date"])
w = s[s.trade_date > s.trade_date.max() - pd.Timedelta(days=90)]
display(w["rag"].value_counts().rename("trading days").to_frame())
print("Today:", w["rag"].iloc[-1], "-", w["rag_reason"].iloc[-1])"""),
("md", "### How the three time series relate (ASX 200, cash rate, volatility)\nDescriptive only: which series move together, not why."),
("code", """from rbabank.transform import relationships
rel = relationships(serve, 90)
iv = rel["index_vs_volatility"]
print(f"ASX 200 vs volatility (90 days): r = {iv['r']} ({iv['strength']}, {iv['days']} days)")
display(pd.DataFrame(rel["rate_events"]).rename(columns={"bps": "change_bp",
        "index_move_pct": "ASX200_next_10d_%", "vol_before": "vol_10d_before_%", "vol_after": "vol_10d_after_%"}))
print("Average change in volatility after a rate move:", rel["avg_vol_change_after_rate_move"], "points")"""),
("md", """## 5. Failure scenarios (run in a separate copy so live data is not touched)
**A. API timeout:** 3 attempts with 2s → 4s backoff, then fall back to the last good raw file and mark the source STALE.
**B. Schema change:** the RBA renames the series ID. The load stops, the file is quarantined, curated data is unchanged."""),
("code", """!rm -rf /content/demo && cp -r /content/rba-asx200-pipeline /content/demo && rm -rf /content/demo/data
%cd /content/demo
!PYTHONPATH=src python -m rbabank.pipeline --offline > /dev/null
print("----- A. timeout -----")
!PYTHONPATH=src python -m rbabank.pipeline --offline --simulate timeout | head -5
print("----- B. schema change -----")
!PYTHONPATH=src python -m rbabank.pipeline --offline --simulate schema_change | head -4
!tail -2 data/quality/quarantine.csv
%cd /content/rba-asx200-pipeline"""),
("md", "## 6. Dashboard (reads only the serve layer)"),
("code", """from rbabank.dashboard import write_dashboard
from rbabank.static_chart import draw
from IPython.display import HTML, Image, display
import html as _h
page = write_dashboard()
draw(illustrative=False)
display(Image("docs/dashboard_static.png"))
display(HTML(f'<iframe srcdoc="{_h.escape(page.read_text())}" style="width:100%;height:1500px;border:0"></iframe>'))"""),
("code", """# Download the live dashboard and chart for the report
from google.colab import files
files.download("docs/dashboard.html"); files.download("docs/dashboard_static.png")"""),
]

nb = {"nbformat": 4, "nbformat_minor": 5,
      "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
                   "colab": {"provenance": []}, "language_info": {"name": "python"}},
      "cells": []}
for i, (kind, src) in enumerate(cells):
    c = {"cell_type": "markdown" if kind == "md" else "code", "id": f"c{i:02d}", "metadata": {},
         "source": src.splitlines(keepends=True)}
    if kind == "code":
        c.update(outputs=[], execution_count=None)
    nb["cells"].append(c)
Path(__file__).with_name("RBA_ASX200_PoC.ipynb").write_text(json.dumps(nb, indent=1))
print("written")
