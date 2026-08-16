# Besë Asset Management — track record

The publisher, the metric engine and the public site for the Besë nominal
$100,000 futures track record.

```
data/inbox/     drop Take Profit Trader or Tradovate exports here
data/archive/   every export ever ingested, named by content hash — the primary source
data/repo/      the publishable data repository (index.json, books/, CHAIN.jsonl)
overrides.json  operator corrections to the broker's grouping — committed, so visible
docs/           the rendered public site, including a copy of the data it renders
bese/           the code
```

## Run it

```bash
python3 autopublish.py              # ingest, rebuild, chain, render
python3 autopublish.py --dry-run    # report only, write nothing
python3 autopublish.py --push       # also git commit and push
python3 -m bese.verify              # re-verify the chain from scratch
python3 tests/test_grouping.py      # run the tests
```

Open `docs/index.html` in a browser, or deploy the `docs/` directory to any
static host — Vercel, Netlify, GitHub Pages, S3. There is no build step, no
dependency and no secret.

## The modules

| File | Does |
|---|---|
| `bese/contracts.py` | Contract specs, NQ-equivalence, the cost rate card |
| `bese/model.py` | `Leg` — the one shape every data source reduces to |
| `bese/sources.py` | Readers for both export formats, plus cross-format matching |
| `bese/group.py` | Round turns → strategy trades, and the override mechanism |
| `bese/normalize.py` | → 1 NQ-equivalent, costs deducted before scaling |
| `bese/session.py` | CME session dating |
| `bese/nav.py` | → the $100,000 nominal NAV series |
| `bese/metrics.py` | **Every published metric. The only place any is calculated.** |
| `bese/chain.py` | Snapshots, hash chain, verification |
| `bese/disclosures.py` | Stamped into every record, not just rendered |
| `bese/site.py` | Renders the site. Computes no metric. Escapes everything. |
| `bese/verify.py` | `python3 -m bese.verify` — re-verify the chain from scratch |
| `tests/test_grouping.py` | The claims the code makes about itself, checked |

## What is deliberately not published

- **The firm's account identifier.** `meta.json` and `trades.csv` carry
  `account_ref: "sha256:…"`. The hash still proves two trades came from the same
  account and still changes when an account is replaced, which is all the record
  claims. Same convention as RVB.
- **The raw exports.** `data/inbox/`, `data/archive/`, `data/tpt/` and `data/raw/` are gitignored. It carries the account
  identifier in the clear and nothing on the site needs it; the operator holds it
  and can share it with anyone who asks.

## The two rules

**No metric is computed in `site.py`.** Everything arrives already calculated in
`metrics.json` and `analytics.json`. That is the only way "the calculation source
is public" is a fact rather than a claim.

**`None` is never `0`.** A withheld or missing value renders as absence — a dash,
a gap, or an explicit *withheld · n/60*.

## Still to do

- OpenTimestamps proofs on each snapshot (the chain proves the series is
  complete; it does not yet prove when each record existed)
- FRED `DGS3MO` risk-free rate (currently `unavailable`, so ratios are gross —
  and every ratio it feeds is gated until 60 sessions anyway)
- A Nasdaq-100 total-return benchmark line
- Resolve the four review-flagged trades and record the decision in
  `overrides.json` (the mechanism is wired and runs; the file is currently empty,
  which is the honest default)
