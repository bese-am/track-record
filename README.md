# Besë Asset Management — track record

A public, verifiable record of beseam.net futures trading strategy, normalised to
1 NQ-equivalent of exposure against a stated $100,000 nominal base.

**This repository exists so the record can be checked without trusting anyone.**
Everything below can be done by a stranger with a clone and no cooperation from
the operator.

## Verify it

```bash
python3 -m bese.verify
```

No dependencies, no configuration, no network. It re-derives every hash from the
files on disk and prints either `chain ok` or the first thing that fails. It
also checks that the copy served by the website is byte-identical to the copy in
this repository.

What that command checks, in order:

**1. Each session record hashes its own content.** Every file in
`data/repo/books/<book>/snapshots/` carries a `hash`: the SHA-256 of its
canonical JSON with the `hash` field removed. Change any published number and it
fails.

```python
import hashlib, json, pathlib

def canonical(payload):
    return json.dumps(payload, sort_keys=True, indent=2,
                      ensure_ascii=False, allow_nan=False,
                      default=str) + "\n"

rec  = json.loads(pathlib.Path("data/repo/books/bese_nominal_100k/"
                               "snapshots/2026-08-14.json").read_text("utf-8"))
body = {k: v for k, v in rec.items() if k != "hash"}
assert hashlib.sha256(canonical(body).encode()).hexdigest() == rec["hash"]
```

**2. The series is complete.** Each record carries the previous session's hash;
the first carries 64 zeroes. Verification walks the snapshot directory rather
than trusting `CHAIN.jsonl` to list its own contents, requires each filename to
match the session date inside it, and requires those dates to increase. A
session cannot be removed, reordered or invented.

**3. Every published file is covered.** A chain over session records protects
session records. So each record also pins the SHA-256 of `nav.csv`,
`trades.csv`, `metrics.json`, `analytics.json`, `archive_manifest.json` and
`overrides.json`, and a digest of `meta.json` excluding the fields derived from
the chain itself.

**4. The numbers follow from the inputs.** `nav.csv` is the whole equity curve.
Every published statistic is computed from it by
`bese.metrics.compute_core_metrics`, which is in this repository. Recompute and
compare:

```python
import csv
nav = list(csv.DictReader(open("data/repo/books/bese_nominal_100k/nav.csv")))
print(float(nav[-1]["equity"]) / float(nav[0]["equity"]) - 1)
```

**5. The records were not back-dated.** Each snapshot carries an OpenTimestamps
proof beside it, anchoring its hash into a Bitcoin block.

```bash
pip install opentimestamps-client
ots info   data/repo/books/bese_nominal_100k/snapshots/2026-08-14.json.ots
ots verify data/repo/books/bese_nominal_100k/snapshots/2026-08-14.json.ots
```

`ots info` reads the proof offline. `ots verify` checks it against the block
chain and therefore needs a Bitcoin Core node — a pruned one is fine. That is
the design working rather than an obstacle: verification is meant to ask you to
trust no third party.

## Where each number is calculated

| File | Responsible for |
|---|---|
| `bese/contracts.py` | Contract specifications, NQ-equivalence, the cost rate card |
| `bese/model.py` | The one shape every data source reduces to |
| `bese/sources.py` | Reading the broker exports, and cross-format matching |
| `bese/group.py` | Fills → strategy trades, and the corrections mechanism |
| `bese/normalize.py` | Scaling to 1 NQ-equivalent, costs deducted before scaling |
| `bese/session.py` | CME session dating |
| `bese/nav.py` | The $100,000 nominal NAV series |
| `bese/metrics.py` | **Every published statistic. The only place any is calculated.** |
| `bese/chain.py` | Session records, hash chain, verification |
| `bese/disclosures.py` | Stamped into every record, not merely rendered on a page |
| `bese/site.py` | Renders the site. Computes no statistic. Escapes every value. |
| `bese/verify.py` | `python3 -m bese.verify` |
| `tests/test_grouping.py` | The claims the code makes about itself, checked |

## Two rules the code holds to

**No statistic is computed in `site.py`.** Everything arrives already calculated
in `metrics.json` and `analytics.json`. That is what makes "the calculation is
public" a fact rather than a claim.

**`None` is never `0`.** A withheld or undefined value renders as absence — a
dash, a gap, or an explicit *withheld · n/60* — never as zero.

## What is deliberately not published

**The account identifier.** `meta.json` and `trades.csv` carry an ordinal label
(`account 1`, `account 2`), which preserves everything the record claims — that
two trades came from the same account, and that the series survived an account
being replaced — and reveals nothing else. A hash of the identifier would not
do: the identifiers have a known shape and a small enough space to enumerate.

**The raw exports.** `data/inbox/`, `data/archive/`, `data/tpt/` and `data/raw/`
are gitignored. One of them carries the account identifier in the clear, and
nothing in the verification above needs them. `archive_manifest.json` records
the SHA-256 of every export the published record was built from, so the sources
are fixed in time without being published, and any one of them can be produced
on request and shown to be the file held on the day.

## Reproduce the record from source

If you hold the raw exports, the record rebuilds from them in full:

```bash
python3 autopublish.py --dry-run   # report only, write nothing
python3 autopublish.py             # ingest, rebuild, chain, render
```

There is no incremental state, no cursor and no "last processed row". Every run
reproduces the entire record from the archive, so the same inputs always produce
the same bytes.
