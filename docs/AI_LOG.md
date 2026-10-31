# AI agent log: what went wrong and how it was caught

The proof-of-concept code was developed with an AI agent (Claude, Anthropic). These are real problems found in its
output during development, kept as evidence for the AI Agent Lens and as the team's review checklist.

## 1. An error handler that hid a real fault (pipeline, Part 1)

- **What the agent wrote.** In `ingest.ingest()`, one `try/except Exception` wrapped both the download *and*
  writing the raw file.
- **Why it was wrong.** If saving the file failed (full disk, wrong permission, a logging error), the pipeline
  would report "source down, using last good file" and carry on. A storage bug would be hidden behind the timeout
  handling. That is a silent failure, which the plan says must never happen.
- **How it was caught.** A demo run piped to `head` raised a `BrokenPipeError` while the raw file was being
  logged. The run log then showed `stale` for a source that had downloaded successfully.
- **Fix.** Only the fetch sits inside the `try`. Errors while writing the file now surface.
- **Rule added.** Reviewer checklist: "Does any try/except catch more than it should?"

## 2. A test whose expected answer was wrong (RAG signal, Part 3)

- **What the agent wrote.** A test that expected the RAG to escalate to RED after several amber days, using a
  10-day history window.
- **Why it was wrong.** The thresholds are rolling percentiles. Each new day enters the window and moves the
  75th percentile, so values that were amber on day 1 were green by day 3. The code was right and the test's
  expectation was wrong.
- **How it was caught.** The test failed. Working the percentiles out by hand showed the code was correct.
- **Fix.** Separate tests with hand-calculated thresholds (p75 = 7.75, p90 = 9.1 for a 1–10 history), and an
  escalation test with a long history where new values barely move the percentiles.
- **Lesson.** A generated test isn't proof. The expected value has to come from a hand calculation.

## 3. A headline that implied causation (dashboard, Part 3)

- **First version.** "The ASX 200 rose 4.0% over 90 days *after* the RBA moved the cash rate up 25 bp…"
- **Why it was wrong for the audience.** Non-technical readers take "after" as "because of". A 90-day chart can't
  show that.
- **Prompt iteration.** The agent was re-prompted: "State what each series did, side by side, in plain words. Do
  not imply the rate move caused the index move."
- **Result.** "The ASX 200 rose 4.0% over the past 90 days. The RBA raised the cash rate 25 bp to 3.85% on 30 Sep,
  and volatility is well above normal and needs attention."

## 4. The same number calculated two different ways (dashboard, Part 3)

- **What the agent wrote.** The "index move after a rate change" was calculated in the chart caption and again in
  the relationships panel, with slightly different windows.
- **Why it was wrong.** The same rate change showed +2.9% in one place and +1.0% in another on the same page. A
  manager who spots that stops trusting the whole dashboard.
- **How it was caught.** Reading the finished page from top to bottom as a manager would.
- **Fix.** One shared calculation (`transform.relationships`) feeds both places.

## 5. Traps checked for explicitly (from the specification)

- **Time zone.** Yahoo stamps each day at Sydney midnight (`2026-08-03 00:00:00+10:00`). Converting to UTC and
  then taking the date gives `2026-08-02`, which shifts every day back by one. The test
  `test_sydney_date_not_shifted_by_utc` proves the code converts to Sydney first.
- **Returns across a gap.** Without the calendar spine, a quarantined day would be skipped and the next return
  would span two days. Fixed by keeping quarantined days as blank rows (`test_return_is_not_stitched_across_a_quarantined_day`).

> Add the student's own examples from running the notebook on live data here (for example, how yfinance's
> column layout or the real RBA file differed from the sample).
