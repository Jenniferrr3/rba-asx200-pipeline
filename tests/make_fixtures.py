"""Builds the ILLUSTRATIVE sample files in tests/fixtures/.

They copy the real file layouts (RBA Table F1 CSV with its metadata header block,
Windows-1252 encoding and dd-Mon-yyyy dates; a yfinance history() export with a
Sydney UTC offset on every timestamp) but the numbers are simulated.
Known data problems are planted on purpose so the tests can prove they are caught.

    python tests/make_fixtures.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)
rng = np.random.default_rng(3022)

# ASX trading holidays in the sample period (weekday closures)
ASX_HOLIDAYS = pd.to_datetime([
    "2024-12-25", "2024-12-26", "2025-01-01", "2025-01-27", "2025-04-18", "2025-04-21",
    "2025-04-25", "2025-06-09", "2025-12-25", "2025-12-26", "2026-01-01", "2026-01-26",
    "2026-04-03", "2026-04-06", "2026-06-08"])
# NSW Bank Holiday (first Monday in August): banks shut, ASX open -> no RBA row that day
NSW_BANK_HOLIDAYS = pd.to_datetime(["2025-08-04", "2026-08-03"])

days = pd.bdate_range("2024-11-01", "2026-10-30")
asx_days = days[~days.isin(ASX_HOLIDAYS)]
rba_days = days[~days.isin(ASX_HOLIDAYS) & ~days.isin(NSW_BANK_HOLIDAYS)]

# ---------------------------------------------------------------- ASX 200
n = len(asx_days)
sigma = np.full(n, 0.0075)
sigma[-32:] = 0.0135            # a late burst of volatility so the RAG signal has something to show
ret = rng.normal(0.0002, sigma)
close = 8150 * np.exp(np.cumsum(ret))
open_ = close * np.exp(rng.normal(0, 0.002, n))
high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
volume = (rng.lognormal(np.log(700e6), 0.18, n) * (1 + 3 * (sigma - 0.0075))).round()

idx = pd.DatetimeIndex(asx_days).tz_localize("Australia/Sydney")   # local midnight, +10:00/+11:00
asx = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                    "Adj Close": close, "Volume": volume.astype("int64")}, index=idx)
asx.index.name = "Date"
asx = asx.round(2)
asx_s = asx.astype(object)

def at(day):
    return asx_s.index.get_loc(pd.Timestamp(day, tz="Australia/Sydney"))

asx_s.iloc[at("2026-09-15"), asx_s.columns.get_loc("Volume")] = 0          # zero volume
asx_s.iloc[at("2026-08-20"), asx_s.columns.get_loc("Close")] = ""          # blank close
r = at("2026-10-07")                                                         # high below low
asx_s.iloc[r, asx_s.columns.get_loc("High")], asx_s.iloc[r, asx_s.columns.get_loc("Low")] = \
    asx_s.iloc[r, asx_s.columns.get_loc("Low")], asx_s.iloc[r, asx_s.columns.get_loc("High")]
dup = asx_s.loc[[asx_s.index[at("2026-10-14")]]]                            # duplicated day
asx_s = pd.concat([asx_s, dup]).sort_index(kind="stable")
asx_s.to_csv(OUT / "asx200_sample.csv")

# ---------------------------------------------------------------- RBA Table F1
rate = pd.Series(4.10, index=rba_days)
rate[rba_days >= "2025-02-19"] = 3.85
rate[rba_days >= "2025-05-21"] = 3.60
rate[rba_days >= "2026-09-30"] = 3.85        # illustrative +25 bp move
interbank = rate - 0.01
interbank[rng.random(len(rba_days)) < 0.01] = np.nan   # occasional blank, as in the real table
bab30 = rate + rng.normal(0.08, 0.02, len(rba_days))

meta = [
    ["F1 INTEREST RATES AND YIELDS – MONEY MARKET", "", "", ""],
    ["Title", "Cash Rate Target", "Interbank Overnight Cash Rate", "EOD 1-month BABs/NCDs"],
    ["Description", "Cash Rate Target", "Interbank Overnight Cash Rate", "1-month BABs/NCDs"],
    ["Frequency", "Daily", "Daily", "Daily"],
    ["Type", "Original", "Original", "Original"],
    ["Units", "Per cent", "Per cent", "Per cent"],
    ["", "", "", ""], ["", "", "", ""],
    ["Source", "RBA", "ASX", "AFMA"],
    ["Publication date", "31-Oct-2026", "31-Oct-2026", "31-Oct-2026"],
]
def rows(series_ids):
    out = meta + [["Series ID", *series_ids]]
    for d, t, i, b in zip(rba_days, rate, interbank, bab30):
        out.append([d.strftime("%d-%b-%Y"), f"{t:.2f}", "" if np.isnan(i) else f"{i:.2f}", f"{b:.3f}"])
    return out

def write(name, data):
    text = "\r\n".join(",".join(r) for r in data) + "\r\n"
    (OUT / name).write_bytes(text.encode("cp1252"))

good = rows(["FIRMMCRTD", "FIRMMCRID", "FIRMMBAB30D"])
pos = next(i for i, r in enumerate(good) if r[0] == "12-Oct-2026")
good[pos][1] = "n/a"                                                          # non-numeric value
write("rba_f1_sample.csv", good)
write("rba_f1_schema_changed.csv", rows(["FIRMMCRT", "FIRMMCRID", "FIRMMBAB30D"]))  # renamed field
print("fixtures written to", OUT)
