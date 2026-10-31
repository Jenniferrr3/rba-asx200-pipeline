# Team standards, handoff and guardrails

This file is for the junior analyst taking over the pipeline. If you can follow it on a Monday without asking
the lead anything, it has done its job.

## 1. Your first day (runbook)

1. Open `notebooks/RBA_ASX200_PoC.ipynb` in Google Colab and run all cells. It clones the repo, runs the tests,
   loads live data and builds the dashboard.
2. Read `docs/METRICS.md`. Every number on the dashboard is defined there.
3. Each morning, check `data/serve/run_status.json` or the "Data health" panel on the dashboard:
   - `stale: true` means a source failed and the dashboard is showing the last good load. See section 4.
   - `rows_quarantined > 0` means open `data/quality/quarantine.csv` and read the `reason` column.

## 2. Layer rules (do not break these)

| Layer | Folder | Rule |
|---|---|---|
| Raw | `data/raw/` | Never edited, never deleted. One file per run, bytes exactly as received. |
| Curated | `data/curated/` | Rebuilt from raw by `validate.py`. Never hand-edited. |
| Serve | `data/serve/` | Rebuilt by `transform.py`. The dashboard reads only this. |
| Quality | `data/quality/` | Append-only logs. Every rejected row appears here with a reason. |

## 3. Data modelling standards

- **Names.** Columns are `snake_case` and include the unit when there is one: `volatility_14d_ann_pct`,
  `cash_rate_change_20d_bps`, `volume_ma20`.
- **Dates.** `trade_date` is always the Sydney trading date (`YYYY-MM-DD`). Timestamps are stored in UTC,
  or with their offset as received. Never take `.date()` of a UTC timestamp for a market date.
- **Grain.** Each table states what one row means in its docstring. `dashboard_daily` has one row per ASX trading day.
- **Thresholds.** Every number lives in `src/rbabank/config.py`. Changing a value there is a reviewed change,
  noted in the pull request with the reason.
- **New metrics.** Write the plain-language definition in `METRICS.md` first (definition, why it matters, edge
  cases). Then write a test with a hand-worked example. Then write the code.
- **Enforcement.** The `tests` GitHub Action runs on every pull request, and `main` is protected so nothing merges
  with a failing test. A reviewer checks the list in section 5.

## 4. When something fails

| Symptom | What the pipeline already did | What you do |
|---|---|---|
| `fetch failed` (timeout or HTTP error) | Retried 3 times (2 s, 4 s waits), kept the last good raw file, marked the source STALE, and failed the GitHub job so you got an email | Check whether the website is up. Re-run with "Run workflow". If it is still down after 2 days, tell the lead. |
| `schema failed` | Stopped that source's load, quarantined the file, left curated data unchanged | Compare `expected` and `received` in `run_log.jsonl`. Update `config.RBA_SERIES` only after confirming the new field means the same thing. Open a pull request. |
| New quarantine reason appears | The row was kept in `quarantine.csv` | Decide whether it is a true error (leave it) or a rule that needs changing (pull request + test). |

## 5. Guardrails for AI-generated code

AI agents are allowed and useful, and their code is treated exactly like a new analyst's code.

1. **Specification first.** Prompt with the plain-language definition from `METRICS.md`, not "write a volatility function".
2. **The author owns it.** Whoever opens the pull request must be able to explain every line. "The AI wrote it" is not a review answer.
3. **Hand-worked tests.** Each metric needs a test whose expected value was calculated by hand (or in a
   spreadsheet), not produced by running the code.
4. **Reviewer checklist** for AI-written changes:
   - Does any `try/except` catch more than it should? (See AI_LOG.md, example 1.)
   - Are dates converted to Sydney before `.date()`?
   - Could a blank become a zero (`fillna(0)`, `sum()` of an empty window)?
   - Is anything dropped (`dropna`, `drop_duplicates`) without being written to quarantine?
   - Does any chart text claim a cause that the data cannot show?
5. **Record misses.** Add any mistake the agent made to `docs/AI_LOG.md`. That record becomes the team's checklist.

## 6. Telling the story (for anyone changing the dashboard)

Start from the reader's question, not the chart. Each panel needs a title, a one-line takeaway and as few lines
as possible. The headline states the "so what" in one sentence. Red, amber and green always appear with a word,
never as colour alone. Don't imply causation: "the index rose after the rate rise" invites a conclusion that a
90-day chart cannot support.

## 7. Priorities and trade-offs (what was deliberately left out)

| Left out | Why | When to add |
|---|---|---|
| Database (CSV files used instead) | Two small sources and a few thousand rows. Files are versioned in Git and a junior analyst can open them. | Beyond ~10 sources, or when several users query the data. Parquet + DuckDB is the next step. |
| Intraday data | The question is about 90-day trends. | Not planned. |
| Third source (e.g. FRED VIX) | Scope limit of 2 sources | Once the first two have run cleanly for a month. Adding one means a new fetcher + validator + tests. Downstream layers do not change. |
| Power BI version | The proof of concept needed to be shareable without a licence | When the bank's BI platform is chosen. `dashboard_daily.csv` is already in the shape Power BI expects. |
