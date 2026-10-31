"""VALIDATE: raw file -> typed rows that passed + rows that failed (with reason).

Two kinds of check:
  1. Schema check (whole file). If the provider renamed or removed a field we expect,
     STOP the load for that source. Guessing which column is "probably" the cash rate
     is how wrong numbers reach a dashboard.
  2. Row rules. Each failing row goes to quarantine with the rule name. Repairs that
     keep the row (e.g. zero volume set to blank) are flagged on the row and counted.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import numpy as np
import pandas as pd

from . import config


class SchemaChangeError(Exception):
    def __init__(self, source: str, expected, received):
        self.source, self.expected, self.received = source, list(expected), list(received)
        super().__init__(f"{source}: schema changed. expected={self.expected} "
                         f"missing={sorted(set(self.expected) - set(self.received))}")


def _decode(raw: bytes) -> str:
    # RBA files have used both UTF-8 (with BOM) and Windows-1252 (en dashes in titles).
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _split(df: pd.DataFrame, bad: pd.Series, reason: str, rejects: list) -> pd.DataFrame:
    if bad.any():
        rejects.append(df[bad].assign(reason=reason))
    return df[~bad]


# ------------------------------------------------------------------ RBA Table F1
def validate_rba(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    rows = list(csv.reader(io.StringIO(_decode(path.read_bytes()))))
    hdr_idx = next((i for i, r in enumerate(rows) if r and r[0].strip() == "Series ID"), None)
    if hdr_idx is None:
        raise SchemaChangeError("rba", ["Series ID header row"], [r[0] for r in rows[:15] if r])
    header = [c.strip() for c in rows[hdr_idx]]
    missing = [s for s in config.RBA_SERIES if s not in header]
    if missing:
        raise SchemaChangeError("rba", config.RBA_SERIES.keys(), header[1:])

    body = [r for r in rows[hdr_idx + 1:] if any(c.strip() for c in r)]
    cols = {s: header.index(s) for s in config.RBA_SERIES}
    df = pd.DataFrame({"date_raw": [r[0].strip() if r else "" for r in body],
                       **{name: [r[cols[s]].strip() if len(r) > cols[s] else "" for r in body]
                          for s, name in config.RBA_SERIES.items()}})
    rows_in, rejects = len(df), []

    df["date"] = pd.to_datetime(df["date_raw"], format="%d-%b-%Y", errors="coerce")
    df = _split(df, df["date"].isna(), "invalid_date", rejects)

    for name in config.RBA_SERIES.values():
        df[name + "_num"] = pd.to_numeric(df[name], errors="coerce")
    tgt = "cash_rate_target"
    df = _split(df, df[tgt].eq(""), "missing_cash_rate_target", rejects)
    df = _split(df, df[tgt + "_num"].isna(), "non_numeric_cash_rate_target", rejects)
    df = _split(df, ~df[tgt + "_num"].between(config.RATE_MIN, config.RATE_MAX),
                "cash_rate_out_of_range", rejects)

    df = df.sort_values("date", kind="stable")
    df = _split(df, df.duplicated("date", keep="first"), "duplicate_date", rejects)
    jump = df[tgt + "_num"].diff().abs() > config.RATE_MAX_DAILY_JUMP
    df = _split(df, jump, "cash_rate_jump_gt_1pp_review", rejects)

    flags = {"interbank_rate_blank_kept": int(df["interbank_overnight_rate_num"].isna().sum())}
    clean = pd.DataFrame({
        "rate_date": df["date"].dt.date,
        "cash_rate_target": df[tgt + "_num"],
        "interbank_overnight_rate": df["interbank_overnight_rate_num"],
    }).reset_index(drop=True)
    rej = pd.concat(rejects)[["date_raw", *config.RBA_SERIES.values(), "reason"]] if rejects \
        else pd.DataFrame(columns=["reason"])
    assert len(clean) + len(rej) == rows_in
    return clean, rej, flags


# ------------------------------------------------------------------ ASX 200 (Yahoo)
def validate_asx(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    received = list(raw.columns)
    date_col = next((c for c in ("Date", "Datetime") if c in received), None)
    if date_col is None or any(c not in received for c in config.ASX_COLUMNS):
        raise SchemaChangeError("asx200", ["Date", *config.ASX_COLUMNS], received)

    df = raw[[date_col, *config.ASX_COLUMNS]].rename(columns={date_col: "timestamp_raw"})
    rows_in, rejects = len(df), []

    # TIME ZONE: the raw stamp is local midnight with an offset, e.g. 2026-08-03 00:00:00+10:00.
    # Parse to an absolute instant (UTC), then convert to Sydney BEFORE taking the date.
    # Taking .date in UTC would shift every trading day back by one calendar day.
    ts = pd.to_datetime(df["timestamp_raw"], utc=True, errors="coerce")
    df["trade_date"] = ts.dt.tz_convert(config.MARKET_TZ).dt.date
    df = _split(df, ts.isna(), "invalid_timestamp", rejects)

    for c in config.ASX_COLUMNS:
        df[c.lower()] = pd.to_numeric(df[c], errors="coerce")
    df = _split(df, df["close"].isna(), "missing_close", rejects)
    o_h_l = df[["open", "high", "low"]].isna().any(axis=1)
    df = _split(df, o_h_l, "missing_open_high_low", rejects)
    bad_ohlc = (df["high"] < df["low"]) | ~df["close"].between(df["low"], df["high"]) \
        | ~df["open"].between(df["low"], df["high"])
    df = _split(df, bad_ohlc, "ohlc_inconsistent", rejects)

    df = df.sort_values("trade_date", kind="stable")
    df = _split(df, df.duplicated("trade_date", keep="first"), "duplicate_trade_date", rejects)
    move = (df["close"] / df["close"].shift(1) - 1).abs() > config.MAX_DAILY_INDEX_MOVE
    df = _split(df, move, "daily_move_gt_15pct_review", rejects)

    # Repair, not reject: price data is valid, but a zero/blank index volume is not a real
    # "nothing traded" day. Blank it so it cannot drag the rolling average down; flag the row.
    bad_vol = df["volume"].isna() | (df["volume"] <= 0)
    df["volume_flag"] = np.where(bad_vol, "zero_or_missing_volume_set_blank", "")
    df.loc[bad_vol, "volume"] = np.nan
    flags = {"zero_or_missing_volume_set_blank": int(bad_vol.sum())}

    clean = df[["trade_date", "timestamp_raw", "open", "high", "low", "close", "volume",
                "volume_flag"]].reset_index(drop=True)
    rej = pd.concat(rejects)[["timestamp_raw", *config.ASX_COLUMNS, "reason"]] if rejects \
        else pd.DataFrame(columns=["reason"])
    assert len(clean) + len(rej) == rows_in
    return clean, rej, flags


def asx_calendar(path: Path) -> list:
    """Every Sydney trading date present in the raw file, including rows later quarantined."""
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    col = "Date" if "Date" in raw.columns else "Datetime"
    ts = pd.to_datetime(raw[col], utc=True, errors="coerce").dropna()
    return sorted(set(ts.dt.tz_convert(config.MARKET_TZ).dt.date))
