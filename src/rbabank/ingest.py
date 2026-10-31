"""INGEST + STORE RAW layer.

Each source is fetched the same way every run, then landed byte-for-byte in
data/raw/<source>/ingest_date=YYYY-MM-DD/<run_id>.csv. Nothing downstream edits raw.

Failure scenario handled here (API timeout / HTTP error):
  retry with a growing delay -> if every attempt fails, log it, keep the last
  good raw file, and mark the run STALE instead of crashing the dashboard.
"""
from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Callable

import requests

from . import config
from .quality import log_event


class SourceUnavailable(Exception):
    """All attempts failed and there is no earlier raw file to fall back to."""


def with_retry(fetch: Callable[[], bytes], source: str, run_id: str,
               attempts: int = config.MAX_ATTEMPTS, base: float = config.BACKOFF_BASE_S,
               sleep: Callable[[float], None] = time.sleep) -> bytes:
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            payload = fetch()
            if not payload:
                raise ValueError("empty response body")
            log_event(run_id, source, "fetch", "ok", attempt=attempt, bytes=len(payload))
            return payload
        except Exception as e:  # timeout, connection error, HTTP 4xx/5xx, empty body
            last_err = e
            log_event(run_id, source, "fetch", "retry" if attempt < attempts else "failed",
                      attempt=attempt, error=f"{type(e).__name__}: {e}")
            if attempt < attempts:
                sleep(base * 2 ** (attempt - 1))
    raise last_err


def land_raw(source: str, payload: bytes, run_id: str) -> Path:
    day = run_id[:4] + "-" + run_id[4:6] + "-" + run_id[6:8]
    path = config.RAW / source / f"ingest_date={day}" / f"{run_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    log_event(run_id, source, "land_raw", "ok", path=str(path.relative_to(config.ROOT)))
    return path


def latest_raw(source: str) -> Path | None:
    files = sorted((config.RAW / source).glob("ingest_date=*/*.csv"))
    return files[-1] if files else None


def ingest(source: str, fetch: Callable[[], bytes], run_id: str, **retry_kw) -> tuple[Path, bool]:
    """Returns (raw file to use, is_stale). Stale = today's fetch failed, using last good file."""
    # Only the fetch is inside the try. An error while writing the raw file is a real
    # fault and must surface, not be mislabelled as "source down, using last good file".
    try:
        payload = with_retry(fetch, source, run_id, **retry_kw)
    except Exception as e:
        fallback = latest_raw(source)
        if fallback is None:
            log_event(run_id, source, "ingest", "failed", reason="no fallback raw file", error=str(e))
            raise SourceUnavailable(source) from e
        log_event(run_id, source, "ingest", "stale", using=str(fallback.relative_to(config.ROOT)))
        return fallback, True
    return land_raw(source, payload, run_id), False


# ------------------------------------------------------------------ real fetchers
def fetch_rba() -> bytes:
    r = requests.get(config.RBA_URL, headers=config.HTTP_HEADERS, timeout=config.HTTP_TIMEOUT_S)
    r.raise_for_status()
    return r.content


def fetch_asx() -> bytes:
    """Yahoo Finance via yfinance. Ticker.history() returns a tz-aware (Sydney) index."""
    import yfinance as yf  # imported here so offline tests do not need it
    df = yf.Ticker(config.ASX_TICKER).history(period=config.HISTORY_PERIOD, interval="1d",
                                             auto_adjust=False, actions=False)
    if df.empty:
        raise ValueError("Yahoo returned no rows")
    buf = io.StringIO()
    df.to_csv(buf)  # keep the index exactly as received, including its UTC offset
    return buf.getvalue().encode()
