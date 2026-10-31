"""Orchestrates one run: ingest -> raw -> validate -> curated -> transform -> serve.

    python -m rbabank.pipeline                 # live: RBA website + Yahoo Finance
    python -m rbabank.pipeline --offline       # sample files in tests/fixtures
    python -m rbabank.pipeline --offline --simulate timeout
    python -m rbabank.pipeline --offline --simulate schema_change
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

from . import config, ingest, transform, validate
from .quality import log_event, log_quality, new_run_id, quarantine, utc_now

FIXTURES = config.ROOT / "tests" / "fixtures"


def _fixture(name: str):
    return lambda: (FIXTURES / name).read_bytes()


def _timeout():
    import requests
    raise requests.exceptions.ReadTimeout("simulated: read timed out after 20s")


def make_fetchers(offline: bool, simulate: str | None) -> dict:
    if offline:
        f = {"rba": _fixture("rba_f1_sample.csv"), "asx200": _fixture("asx200_sample.csv")}
    else:
        f = {"rba": ingest.fetch_rba, "asx200": ingest.fetch_asx}
    if simulate == "timeout":
        f["rba"] = _timeout
    elif simulate == "schema_change":
        f["rba"] = _fixture("rba_f1_schema_changed.csv")
    return f


VALIDATORS = {"rba": validate.validate_rba, "asx200": validate.validate_asx}
CURATED_FILES = {"rba": "rba_cash_rate_daily.csv", "asx200": "asx200_daily.csv"}


def load_source(source: str, fetch, run_id: str, status: dict, **retry_kw) -> None:
    st = status.setdefault(source, {})
    try:
        raw_path, stale = ingest.ingest(source, fetch, run_id, **retry_kw)
    except ingest.SourceUnavailable:
        st.update(loaded=False, stale=True, reason="fetch failed and no earlier raw file")
        return
    st.update(raw_file=str(raw_path.relative_to(config.ROOT)), stale=stale)
    if stale:   # fetch failed -> previous curated table stays as it is
        st.update(loaded=False, reason="fetch failed after retries; showing last good load")
        return

    try:
        clean, rejects, flags = VALIDATORS[source](raw_path)
    except validate.SchemaChangeError as e:
        # Stop the load rather than guess. The whole file is quarantined (by reference),
        # the curated table is left untouched, and the dashboard shows the source as stale.
        log_event(run_id, source, "schema", "failed", expected=e.expected, received=e.received)
        quarantine(run_id, source, pd.DataFrame([{
            "reason": "schema_change_file_not_loaded", "raw_file": raw_path.name,
            "missing_fields": sorted(set(e.expected) - set(e.received))}]))
        st.update(loaded=False, stale=True, reason=f"schema change: {e}")
        return

    quarantine(run_id, source, rejects)
    log_quality(run_id, source, len(clean) + len(rejects), len(clean), rejects, flags)
    config.CURATED.mkdir(parents=True, exist_ok=True)
    clean.to_csv(config.CURATED / CURATED_FILES[source], index=False)
    if source == "asx200":
        pd.DataFrame({"trade_date": validate.asx_calendar(raw_path)}).to_csv(
            config.CURATED / "asx200_calendar.csv", index=False)
    st.update(loaded=True, rows_passed=len(clean), rows_quarantined=len(rejects),
              flags=flags, last_success_utc=utc_now())
    log_event(run_id, source, "curated", "ok", rows=len(clean), quarantined=len(rejects))


def run(offline: bool = False, simulate: str | None = None, **retry_kw) -> dict:
    run_id = new_run_id()
    status_path = config.SERVE / "run_status.json"
    prev = json.loads(status_path.read_text()) if status_path.exists() else {}
    status = {"run_id": run_id, "started_utc": utc_now(), "mode": "offline" if offline else "live",
              "simulate": simulate, "sources": {}}
    for source, fetch in make_fetchers(offline, simulate).items():
        load_source(source, fetch, run_id, status["sources"], **retry_kw)
        # carry the last success time forward so the dashboard can show data freshness
        s = status["sources"][source]
        if not s.get("loaded"):
            s["last_success_utc"] = prev.get("sources", {}).get(source, {}).get("last_success_utc")

    have = all((config.CURATED / f).exists() for f in CURATED_FILES.values())
    if not have:
        log_event(run_id, "all", "serve", "skipped", reason="a curated table has never loaded")
        status["serve_built"] = False
    else:
        asx = pd.read_csv(config.CURATED / "asx200_daily.csv")
        rba = pd.read_csv(config.CURATED / "rba_cash_rate_daily.csv")
        cal = pd.read_csv(config.CURATED / "asx200_calendar.csv")["trade_date"]
        serve = transform.build_serve(asx, rba, calendar=cal)
        config.SERVE.mkdir(parents=True, exist_ok=True)
        serve.to_csv(config.SERVE / "dashboard_daily.csv", index=False)
        status["serve_built"] = True
        status["latest_trade_date"] = str(serve["trade_date"].iloc[-1])
        log_event(run_id, "all", "serve", "ok", rows=len(serve))
    status["finished_utc"] = utc_now()
    config.SERVE.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, indent=2, default=str))
    return status


def reset_data() -> None:
    """Delete generated data (raw, curated, serve, quality). For demos and tests only."""
    for d in (config.RAW, config.CURATED, config.SERVE, config.QUALITY):
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use sample files in tests/fixtures")
    ap.add_argument("--simulate", choices=["timeout", "schema_change"])
    ap.add_argument("--dashboard", action="store_true", help="also write docs/dashboard.html")
    a = ap.parse_args()
    s = run(offline=a.offline, simulate=a.simulate)
    print(json.dumps(s, indent=2, default=str))
    if a.dashboard and s.get("serve_built"):
        from .dashboard import write_dashboard
        print("dashboard ->", write_dashboard())
