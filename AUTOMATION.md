# Automating the Besë publisher

## What can and cannot be automated

**Tradovate's native API is not available to you.** Tradovate gates API credentials behind a live account with $1,000 equity plus a $25/month subscription, and it **excludes prop-firm and evaluation accounts from the API programme entirely, regardless of balance** — they are not standard funded client accounts. Prop firms independently disable personal API keys on both evaluation and funded accounts. So "connect Besë to Tradovate and let it pull" is not a route that exists for your setup, and no amount of engineering opens it.

What that leaves is one manual action: **click Export in Tradovate.** Roughly ten seconds, as often as you like.

Everything after that click is now automated. That is not a consolation prize — the export was never the hard part. The hard part was making repeated, overlapping exports safe to ingest, and that is done.

## The design that makes it safe

```
   Tradovate  --export-->  data/inbox/  --sweep-->  data/archive/
                                                         |
                                          full rebuild from ALL archived exports
                                                         |
                                    trades.csv · nav.csv · summary.json · preview.html
                                                         |
                                                  git commit + push
```

Four properties, each of which exists to prevent a specific way an unattended pipeline goes wrong:

**Re-importing is a no-op.** Tradovate exports the period to date, so consecutive pulls overlap by design. Round turns are keyed on the broker's own `(buyFillId, sellFillId)`. Drop the same file in twice, drop a full-year export beside a monthly one, re-run the whole archive from scratch — same answer every time. Verified: 39 rows across three overlapping files collapse to 15 round turns and the identical NAV.

**Trade IDs come from broker fill IDs, never a counter.** A sequence number is a function of everything else in the file, so a longer export would renumber trades that had already been published and hash-chained. `20260813-607988600295` is a function of the trade alone.

**It rebuilds everything, every run.** No cursor, no "last processed row", no incremental state to drift out of step with reality. A bad run is fixed by running again. This is the property RVB gets by reading its parquet archive rather than its live database.

**It refuses rather than guesses.** Four abort conditions, each exiting non-zero and writing nothing:

| Condition | Why it matters |
|---|---|
| The same round turn reported differently in two exports | One file is not what it claims to be |
| P&L does not reconcile against the contract multiplier | A contract spec is wrong; every downstream number is too |
| The record would shrink | An export is missing from the archive — the failure mode a track record must never absorb quietly |
| Daily returns do not compound to the cumulative return | The NAV series is internally inconsistent |

Nothing in the inbox is ever deleted; files move to `archive/` named by content hash and stay there. The archive is the primary source. The book files are derived and can be thrown away and rebuilt at any time.

## Setup

**1. Point Tradovate's export at the inbox.** Set your browser's download directory to `data/inbox/`, or just drag the file there. Filenames do not matter.

**2. Run it.**

```bash
python3 autopublish.py            # normal run
python3 autopublish.py --dry-run  # rebuild, report, write nothing
python3 autopublish.py --push     # also git commit and push
```

**3. Put it on a schedule.** The publisher must run on **your** machine, not in a cloud session — the same reason RVB publishes from its own hardware: *"GitHub Actions is not involved in producing this data and holds no broker credential."*

macOS / Linux — `crontab -e`:

```cron
# every weekday at 22:15 London, after the US close
15 22 * * 1-5  cd /path/to/bese && /usr/bin/python3 autopublish.py --push >> data/cron.log 2>&1
```

Windows — Task Scheduler, daily trigger, action `python.exe C:\path\to\bese\autopublish.py --push`.

A run with an empty inbox and no new data is harmless: it rebuilds, confirms nothing changed, and exits. So scheduling it daily costs nothing even if you only export weekly.

**4. Watch the log.** `data/publish.log` accumulates every run. An abort is loud, exits non-zero, and leaves the published record untouched.

## What still needs you

Only the thing that genuinely needs judgement: **review flags.** When two same-direction trades in one instrument sit minutes apart and share no fill ID, the publisher cannot tell a deliberate scale-in from two separate re-entries. It flags them, never merges them, and says so at the end of the run. You resolve it once in a committed `overrides.yaml`, and it stays resolved.

Four are outstanding in the current record (08-11 and 08-13).

## If you want to remove the export click too

Three options, in descending order of how much I would recommend them:

1. **Ask your prop firm directly** whether they offer any data export or read-only API of their own. Several run their own dashboards on top of Tradovate and this is firm-specific rather than a Tradovate limitation. Cheapest thing to check, and it costs one email.
2. **A copy-trade bridge you already run.** Webhook services (PickMyTrade and similar) sit between TradingView and Tradovate and keep their own execution logs, which can be exported or polled. This only helps if you are already using one — do not add a $65/month dependency to save ten seconds.
3. **Browser automation against the Tradovate web UI.** Technically possible, and I would advise against it: it means storing your broker credentials somewhere a script can reach them, and it breaks silently whenever the UI changes. The failure mode is a track record that stops updating without telling you — worse than a manual click.

---

Sources: [Tradovate community forum on prop-firm API access](https://community.tradovate.com/t/how-can-i-use-tradovate-apis-for-prop-firm-eval-and-paid-accounts/7814) · [Tradovate API access requirements, 2026](https://blog.pickmytrade.trade/tradovate-api-access-without-1000-minimum-2026-options/) · [Prop firms using Tradovate](https://www.quantvps.com/blog/prop-firms-that-use-tradovate)
