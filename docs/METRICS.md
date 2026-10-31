# Metric requirements (plain language, written before any code)

Business question: *How are markets trending, so bank performance can later be benchmarked against industry data?*
Dashboard question: *How has market activity trended over the past 90 days, and is there anything we should be watching?*

These definitions were written first and then given to the AI agent as the specification. The tests in
`tests/test_pipeline.py` check the code against numbers worked out by hand from these definitions.

## Shared rules for every metric

| Rule | Decision |
|---|---|
| What one row means | One ASX trading day, dated in Sydney local time (`Australia/Sydney`). |
| Which calendar wins | The ASX calendar. Only days the ASX traded appear. RBA values are attached to those days. |
| Windows | Counted in **trading days**, not calendar days. |
| Missing days | A quarantined trading day stays on the calendar as a blank row. No value is calculated across it. |
| Blank vs zero | A blank is never treated as zero. |
| Look-ahead | No metric ever uses a value dated after the row's date. |

## M1. 20-day average volume

- **Definition.** The average daily traded volume of the ASX 200 over the last 20 trading days, including today.
- **Why it matters.** It shows whether market activity is above or below its recent normal. Busy markets
  usually mean news, repricing or stress. When benchmarking, the bank's own trading activity should be
  compared with this, not with a single day.
- **Edge cases.** It needs at least 15 valid volume days out of the 20, otherwise it is blank. Zero or missing
  index volume is set to blank (and flagged), so it is skipped rather than pulling the average down.
- **Shown as.** A line over the daily volume bars, plus "today vs 20-day average, %".

## M2. 14-day volatility (annualised)

- **Definition.** The spread of daily returns over the last 14 trading days. Daily return = natural log of
  (today's close ÷ previous trading day's close). The spread is the sample standard deviation of those 14
  returns, multiplied by √252 to express it as a yearly % (so it compares with figures quoted in industry reports).
- **Why it matters.** It shows whether the market is calmer or more unsettled than usual. Benchmarks taken in a
  high-volatility period are less reliable, so this metric drives the RAG signal.
- **Edge cases.** It needs at least 12 valid returns out of the 14. A return is blank if either day is blank.
  The first 14 days of history are blank.

## M3. 20-day rate of change

- **Definition.** The percentage change in the index close compared with the close 20 trading days earlier.
- **Why it matters.** It shows how fast the market is moving and in which direction. It is the simplest number
  to compare with the bank's own month-on-month performance.
- **Edge cases.** It is blank for the first 20 days, and blank if either close is blank.

## Macro alignment: cash rate target

- **Definition.** The RBA cash rate target in force on each trading day: the latest RBA value dated on or before
  that day.
- **Why it matters.** The policy rate drives funding costs and asset prices. Showing it beside the index lets a
  reader see whether the market is moving with policy or diverging from it.
- **Edge cases.** If the RBA has no row for an ASX trading day (for example, the NSW Bank Holiday in August,
  when banks close but the ASX trades), the previous value is carried forward and flagged. If the value is more
  than 5 days old, it is flagged as stale.
- **Assumption to check with the RBA documentation.** The target is dated from its *effective* date. A rate
  decision is announced at 2:30 pm Sydney time, before the 4:00 pm ASX close, so the market reacts on the
  announcement day, one trading day before the new rate appears in the table. The dashboard marks the change
  on the effective date and says so.

## RAG signal (on M2)

| State | Rule | What the reader should do |
|---|---|---|
| GREEN | Volatility is below the 75th percentile of the previous 252 trading days | Nothing. Benchmarks are comparable. |
| AMBER | Between the 75th and 90th percentile | Watch. Re-check over the next two weeks before drawing benchmark conclusions. |
| RED | At or above the 90th percentile, **or** AMBER for 5 trading days in a row | Treat short-term benchmark comparisons with caution and brief the desk. |
| GREY | Fewer than 120 prior trading days | The signal can't be judged yet. |

**Why these thresholds.** Percentiles of the index's *own* recent history adapt to the market regime, so the
signal means "unusual for this market". A fixed number such as 20% would be permanently red in a crisis year and
never fire in a calm one. By construction, the 75th and 90th percentiles fire roughly 25% and 10% of the time,
which is about one week in four flagged. That is often enough to be useful but not so often that people ignore
it. The 5-day escalation catches a slow, persistent rise that never reaches the 90th percentile. The notebook
prints how often each state fired over the last 90 days, so the threshold can be reviewed against real data.

**Known weakness.** In a long volatile spell, the high values enter the 252-day history and raise the thresholds,
so a "new normal" gradually stops looking unusual. The sample data shows this: the red threshold rises from about
16% to about 21% in late October. The plan includes a quarterly threshold review.
