"""Run log, data-quality log and quarantine.

Rule: a record that fails a check is never silently dropped. It is written to the
quarantine file with the rule it failed, and the counts go to the data-quality log.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pandas as pd

from . import config


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append_csv(df: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, mode="a", header=not path.exists(), index=False)


def log_event(run_id: str, source: str, stage: str, status: str, **detail) -> None:
    """One JSON line per event: attempts, failures, loads. Easy to grep, easy to load."""
    config.QUALITY.mkdir(parents=True, exist_ok=True)
    rec = {"ts_utc": utc_now(), "run_id": run_id, "source": source,
           "stage": stage, "status": status, **detail}
    with open(config.QUALITY / "run_log.jsonl", "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(f"[{rec['ts_utc']}] {source:<6} {stage:<10} {status:<8} {detail if detail else ''}")


def quarantine(run_id: str, source: str, rejects: pd.DataFrame) -> None:
    """Write failed rows, with reason, as strings so nothing is lost to type coercion."""
    if rejects.empty:
        return
    out = rejects.astype(str).copy()
    out.insert(0, "source", source)
    out.insert(0, "run_id", run_id)
    out.insert(0, "quarantined_at_utc", utc_now())
    # Keep a fixed column order: run metadata, reason, then the record as one JSON string.
    record_cols = [c for c in out.columns if c not in ("quarantined_at_utc", "run_id", "source", "reason")]
    out["record"] = out[record_cols].to_dict(orient="records")
    out["record"] = out["record"].apply(json.dumps)
    _append_csv(out[["quarantined_at_utc", "run_id", "source", "reason", "record"]],
                config.QUALITY / "quarantine.csv")


def log_quality(run_id: str, source: str, rows_in: int, rows_passed: int,
                rejects: pd.DataFrame, flags: dict | None = None) -> pd.DataFrame:
    """One row per rule per run, including rules that found nothing (zero is evidence too)."""
    by_rule = rejects["reason"].value_counts().to_dict() if not rejects.empty else {}
    rows = [{"ts_utc": utc_now(), "run_id": run_id, "source": source, "rule": "_total",
             "rows_in": rows_in, "rows_passed": rows_passed,
             "rows_quarantined": len(rejects), "rows_flagged": 0}]
    for rule, n in sorted(by_rule.items()):
        rows.append({"ts_utc": utc_now(), "run_id": run_id, "source": source, "rule": rule,
                     "rows_in": rows_in, "rows_passed": rows_passed, "rows_quarantined": n,
                     "rows_flagged": 0})
    for rule, n in (flags or {}).items():   # repaired / flagged but kept
        rows.append({"ts_utc": utc_now(), "run_id": run_id, "source": source,
                     "rule": f"flag:{rule}", "rows_in": rows_in, "rows_passed": rows_passed,
                     "rows_quarantined": 0, "rows_flagged": n})
    df = pd.DataFrame(rows)
    _append_csv(df, config.QUALITY / "dq_log.csv")
    assert rows_passed + len(rejects) == rows_in, "row reconciliation failed: rows lost"
    return df
