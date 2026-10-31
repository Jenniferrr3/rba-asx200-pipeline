"""Tests. Every metric is checked against numbers worked by hand, not against itself.

    PYTHONPATH=src pytest -q
"""
import math
import shutil

import numpy as np
import pandas as pd
import pytest
import requests

from rbabank import config, ingest, pipeline, transform, validate

FIX = config.ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Each test writes to its own temp data folder, never the real one."""
    for name in ("RAW", "CURATED", "SERVE", "QUALITY"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "tests").mkdir()
    shutil.copytree(FIX, tmp_path / "tests" / "fixtures")
    monkeypatch.setattr(pipeline, "FIXTURES", tmp_path / "tests" / "fixtures")
    yield


# ------------------------------------------------------------------ metrics, hand-worked
def frame(close, volume=None):
    n = len(close)
    return pd.DataFrame({"trade_date": pd.bdate_range("2026-01-05", periods=n),
                         "close": close, "volume": volume if volume is not None else [1.0] * n,
                         "cash_rate_target": [3.6] * n})


def test_rolling_volume_average_by_hand(monkeypatch):
    monkeypatch.setattr(config, "VOL_MA_WINDOW", 3)
    monkeypatch.setattr(config, "VOL_MA_MIN_OBS", 2)
    d = transform.add_metrics(frame([1, 1, 1, 1, 1], [100, 200, np.nan, 400, 600]))
    # day1: 1 obs < 2 -> blank; day2: (100+200)/2=150; day3: blank skipped (100+200)/2=150
    # day4: (200+400)/2=300; day5: (400+600)/2=500  (the blank is skipped, never treated as 0)
    assert math.isnan(d["volume_ma20"][0])
    assert d["volume_ma20"][1:].tolist() == [150, 150, 300, 500]


def test_volatility_by_hand(monkeypatch):
    monkeypatch.setattr(config, "VOLATILITY_WINDOW", 3)
    monkeypatch.setattr(config, "VOLATILITY_MIN_OBS", 3)
    close = [100, 101, 99, 102]
    d = transform.add_metrics(frame(close))
    r = [math.log(101 / 100), math.log(99 / 101), math.log(102 / 99)]
    m = sum(r) / 3
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / 2)        # sample s.d., n-1
    assert d["volatility_14d_ann_pct"].iloc[-1] == pytest.approx(sd * math.sqrt(252) * 100)
    assert d["volatility_14d_ann_pct"].iloc[:3].isna().all()  # first window not complete


def test_rate_of_change_by_hand(monkeypatch):
    monkeypatch.setattr(config, "ROC_WINDOW", 2)
    d = transform.add_metrics(frame([100, 105, 110, 99]))
    assert d["roc_20d_pct"].iloc[2] == pytest.approx(10.0)    # 110/100 - 1
    assert d["roc_20d_pct"].iloc[3] == pytest.approx(-5.714285714)  # 99/105 - 1


def test_return_is_not_stitched_across_a_quarantined_day():
    asx = pd.DataFrame({"trade_date": ["2026-08-19", "2026-08-21"], "close": [100.0, 110.0],
                        "volume": [1.0, 1.0]})
    rba = pd.DataFrame({"rate_date": ["2026-08-19"], "cash_rate_target": [3.6],
                        "interbank_overnight_rate": [3.59]})
    cal = ["2026-08-19", "2026-08-20", "2026-08-21"]           # 20 Aug was quarantined
    d = transform.add_metrics(transform.align(asx, rba, cal))
    assert d["log_return"].isna().all()                        # no 100 -> 110 'one-day' jump


# ------------------------------------------------------------------ RAG
def test_rag_thresholds(monkeypatch):
    monkeypatch.setattr(config, "RAG_LOOKBACK", 10)
    monkeypatch.setattr(config, "RAG_MIN_HISTORY", 10)
    vols = list(range(1, 11)) + [5]               # prior window 1..10
    d = transform.add_rag(pd.DataFrame({"volatility_14d_ann_pct": vols}))
    assert d["rag"][:10].eq("GREY").all()        # not enough history yet
    # by hand, linear interpolation: p75 = 7 + 0.75*(8-7) = 7.75 ; p90 = 9 + 0.1*(10-9) = 9.1
    assert d["vol_p75"][10] == pytest.approx(7.75) and d["vol_p90"][10] == pytest.approx(9.1)
    assert d["rag"][10] == "GREEN"
    for v, want in [(7.75, "AMBER"), (9.0, "AMBER"), (9.1, "RED")]:
        dd = transform.add_rag(pd.DataFrame({"volatility_14d_ann_pct": list(range(1, 11)) + [v]}))
        assert dd["rag"][10] == want, v


def test_rag_amber_five_days_in_a_row_escalates(monkeypatch):
    monkeypatch.setattr(config, "RAG_LOOKBACK", 100)
    monkeypatch.setattr(config, "RAG_MIN_HISTORY", 100)
    vols = list(range(1, 101)) + [80] * 6         # 80 sits between p75 (~75) and p90 (~90)
    d = transform.add_rag(pd.DataFrame({"volatility_14d_ann_pct": vols}))
    assert d["rag"][100:].tolist() == ["AMBER"] * 4 + ["RED"] * 2
    assert d["rag_reason"][104] == "Amber for 5+ trading days in a row"


# ------------------------------------------------------------------ time zone + calendar
def test_sydney_date_not_shifted_by_utc():
    ts = pd.to_datetime(pd.Series(["2026-08-03 00:00:00+10:00"]), utc=True)
    assert str(ts.dt.date[0]) == "2026-08-02"                  # the classic bug
    assert str(ts.dt.tz_convert(config.MARKET_TZ).dt.date[0]) == "2026-08-03"  # what we do


def test_asx_dates_survive_daylight_saving_change():
    clean, rej, _ = validate.validate_asx(FIX / "asx200_sample.csv")
    dates = set(map(str, clean["trade_date"]))
    assert {"2026-04-02", "2026-04-07"} <= dates               # AEDT -> AEST over Easter
    assert all(pd.Timestamp(d).dayofweek < 5 for d in dates)   # no weekend dates appear


def test_cash_rate_carried_forward_on_nsw_bank_holiday():
    asx, _, _ = validate.validate_asx(FIX / "asx200_sample.csv")
    rba, _, _ = validate.validate_rba(FIX / "rba_f1_sample.csv")
    m = transform.align(asx, rba)
    row = m[m["trade_date"] == "2026-08-03"].iloc[0]           # ASX open, banks shut
    assert row["rate_carried_forward"] and str(row["rate_date"].date()) == "2026-07-31"
    assert not m["rate_stale_flag"].any()


def test_never_uses_a_future_rate():
    asx = pd.DataFrame({"trade_date": ["2026-09-29"], "close": [1.0], "volume": [1.0]})
    rba = pd.DataFrame({"rate_date": ["2026-09-28", "2026-09-30"], "cash_rate_target": [3.6, 3.85],
                        "interbank_overnight_rate": [3.59, 3.84]})
    assert transform.align(asx, rba)["cash_rate_target"].iloc[0] == 3.6


# ------------------------------------------------------------------ quality: nothing silently dropped
def test_planted_asx_problems_are_quarantined_with_reasons():
    clean, rej, flags = validate.validate_asx(FIX / "asx200_sample.csv")
    assert sorted(rej["reason"]) == ["duplicate_trade_date", "missing_close", "ohlc_inconsistent"]
    raw_rows = len(pd.read_csv(FIX / "asx200_sample.csv"))
    assert len(clean) + len(rej) == raw_rows                   # every row accounted for
    assert flags["zero_or_missing_volume_set_blank"] == 1
    z = clean[clean["volume_flag"] != ""]
    assert str(z["trade_date"].iloc[0]) == "2026-09-15" and z["volume"].isna().all()


def test_planted_rba_problem_is_quarantined():
    clean, rej, _ = validate.validate_rba(FIX / "rba_f1_sample.csv")
    assert rej["reason"].tolist() == ["non_numeric_cash_rate_target"]
    assert rej["date_raw"].tolist() == ["12-Oct-2026"]


# ------------------------------------------------------------------ failure scenarios
def test_timeout_retries_with_backoff_then_raises():
    waits, calls = [], []

    def flaky():
        calls.append(1)
        raise requests.exceptions.ReadTimeout("timed out")
    with pytest.raises(requests.exceptions.ReadTimeout):
        ingest.with_retry(flaky, "rba", "test", attempts=3, base=2, sleep=waits.append)
    assert len(calls) == 3 and waits == [2, 4]


def test_timeout_recovers_on_second_attempt():
    tries = iter([requests.exceptions.ConnectTimeout("x"), None])

    def fetch():
        e = next(tries)
        if e:
            raise e
        return b"ok"
    assert ingest.with_retry(fetch, "rba", "t", sleep=lambda s: None) == b"ok"


def test_timeout_falls_back_to_last_good_load_and_marks_stale():
    pipeline.run(offline=True)                                       # day 1: good load
    before = pd.read_csv(config.SERVE / "dashboard_daily.csv")
    s = pipeline.run(offline=True, simulate="timeout", sleep=lambda x: None)  # day 2: RBA down
    assert s["sources"]["rba"]["stale"] is True and s["serve_built"] is True
    assert s["sources"]["rba"]["last_success_utc"] is not None
    after = pd.read_csv(config.SERVE / "dashboard_daily.csv")
    pd.testing.assert_frame_equal(before, after)                     # dashboard still has data
    log = pd.read_json(config.QUALITY / "run_log.jsonl", lines=True)
    assert (log["status"] == "failed").any() and (log["status"] == "stale").any()


def test_schema_change_stops_the_load_and_keeps_curated():
    pipeline.run(offline=True)
    curated_before = (config.CURATED / "rba_cash_rate_daily.csv").read_text()
    s = pipeline.run(offline=True, simulate="schema_change")
    assert s["sources"]["rba"]["loaded"] is False and "schema" in s["sources"]["rba"]["reason"]
    assert (config.CURATED / "rba_cash_rate_daily.csv").read_text() == curated_before
    q = pd.read_csv(config.QUALITY / "quarantine.csv")
    assert (q["reason"] == "schema_change_file_not_loaded").any()
    assert "FIRMMCRTD" in q.loc[q["reason"] == "schema_change_file_not_loaded", "record"].iloc[0]


def test_raw_file_is_byte_identical_to_what_was_received():
    pipeline.run(offline=True)
    landed = next((config.RAW / "rba").glob("*/*.csv")).read_bytes()
    assert landed == (FIX / "rba_f1_sample.csv").read_bytes()


# ------------------------------------------------------------------ relationships
def test_relationships_event_window_by_hand(monkeypatch):
    n = 30
    close = [100.0] * n
    close[16:] = [110.0] * (n - 16)                 # index jumps after the change on day 15
    rate = [3.6] * 15 + [3.85] * (n - 15)
    vol = [10.0] * 15 + [20.0] * (n - 15)
    s = pd.DataFrame({"trade_date": pd.bdate_range("2026-01-05", periods=n), "close": close,
                      "cash_rate_target": rate, "rate_changed_today": [False] * 15 + [True] + [False] * (n - 16),
                      "roc_20d_pct": np.linspace(-5, 5, n), "volatility_14d_ann_pct": vol})
    out = transform.relationships(s, window_days=60, event_days=10)
    e = out["rate_events"][0]
    assert e["bps"] == 25 and e["index_move_pct"] == 10.0        # 110/100 - 1, by hand
    assert e["vol_before"] == 10.0 and e["vol_after"] == 20.0
    assert out["avg_vol_change_after_rate_move"] == 10.0
    assert out["index_vs_volatility"]["r"] > 0.6 and out["index_vs_volatility"]["strength"] == "strong"
