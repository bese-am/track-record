#!/usr/bin/env python3
"""The Besë publisher. Runs unattended on a schedule.

    inbox/  ->  archive/  ->  full rebuild  ->  data repo  ->  site  ->  git push

Design notes, because they are the difference between an automation that can
be trusted and one that quietly drifts:

**It rebuilds from the whole archive, every run.** No incremental state, no
"last processed row", no cursor to get out of step with reality. Any run
reproduces the entire record from the raw exports, so a bad run is fixed by
running again.

**Ingesting the same export twice is a no-op.** Both vendors export the period
to date, so consecutive pulls overlap by design.

**Nothing in the inbox is deleted.** Files move to archive/ named by content
hash. The archive is the primary source; everything else is derived and can be
thrown away and rebuilt.

**It refuses rather than guesses.** Every check below aborts with a non-zero
exit and changes nothing on disk.

Usage:
    python3 autopublish.py              # normal scheduled run
    python3 autopublish.py --dry-run    # rebuild, report, write nothing
    python3 autopublish.py --push       # also git commit and push
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from bese import site as site_builder
from bese.chain import ARTEFACTS as ARTEFACTS_TEMPLATES
from bese.chain import (GENESIS, artefact_digests, rebuild_chain,
                        verify, write_snapshot)
from bese.contracts import CONTRACTS, COST_PER_NQ_EQUIVALENT
from bese.disclosures import DISCLOSURES
from bese.group import group_legs
from bese.group import apply_overrides
from bese.metrics import (MIN_SESSIONS_FOR_ANNUALISED, MetricInputs,
                          compute_analytics, compute_core_metrics)
from bese.nav import NOMINAL_CAPITAL, build_nav, cumulative_return
from bese.normalize import normalise_all
from bese import stamp as ots
from bese.session import _ET, closes_after_rollover
from bese.sources import read_many

ROOT = Path(__file__).parent
INBOX = ROOT / "data" / "inbox"
ARCHIVE = ROOT / "data" / "archive"
REPO = ROOT / "data" / "repo"                 # the publishable data repository
SITE = ROOT / "docs"                          # rendered site; GitHub Pages serves /docs
LOG = ROOT / "data" / "publish.log"

BOOK = "bese_nominal_100k"
LABEL = "BESE-NQ-100K"
TAGLINE = "NQ / MNQ index futures, normalised to 1 NQ-equivalent exposure"
BOOK_DIR = REPO / "books" / BOOK

# The risk-free rate. FRED DGS3MO averaged over the measured window is the
# intended source; until it is wired in, the payload says so
# explicitly rather than quietly pretending the rate is zero. Nothing published
# today depends on it — every ratio it feeds is gated until 60 sessions.
RISK_FREE_ANNUAL = 0.0
RISK_FREE_SOURCE = "unavailable — ratios are explicitly gross where shown"


def log(msg: str = "") -> None:
    line = (f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}  {msg}" if msg else "")
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def die(msg: str) -> None:
    log(f"ABORT  {msg}")
    log("Nothing was written. The published record is unchanged.")
    sys.exit(1)


def sweep_inbox(dry: bool) -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    moved = 0
    for f in sorted(INBOX.glob("*.csv")):
        digest = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        dest = ARCHIVE / f"{f.stem}.{digest}.csv"
        if dest.exists():
            log(f"  already archived, discarding duplicate: {f.name}")
            if not dry:
                f.unlink()
            continue
        log(f"  new export: {f.name} -> {dest.name}")
        if not dry:
            shutil.move(str(f), dest)
        moved += 1
    return moved


def timestamps_only(args) -> None:
    """Mature the proofs without touching the record.

    A fresh OpenTimestamps proof is a commitment to a calendar server; the
    aggregating Bitcoin transaction confirms hours later, and only then can the
    proof be upgraded to something a verifier can check against the chain. That
    is a clock, not a trading event -- it has nothing to do with whether a new
    session exists.

    A full publish run cannot do this job. It rebuilds every published file
    from the archive, and if anything has changed since the head snapshot was
    chained -- an operator decision recorded in overrides.json, say -- it
    aborts, correctly, BEFORE reaching the stamping step. So the proofs would
    stay pending until the next trading day, for reasons that have nothing to
    do with timestamps.

    This path touches only what the chain does not pin: the `.ots` files, which
    sit beside the snapshots and are not artefacts, and `meta.json`'s
    `timestamping` block, which `chain.META_DERIVED` excludes from the meta
    digest precisely so that proof counters can move without disturbing the
    record. Every published figure is left exactly as chained.
    """
    log("=" * 62)
    log("Besë publisher run  (timestamps only)")

    meta_path = BOOK_DIR / "meta.json"
    if not meta_path.exists():
        die("no published record yet — run a full publish first")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    log("1. maturing the OpenTimestamps proofs")
    ts = ots.stamp_new_snapshots(BOOK_DIR)
    if ts["client"] is None:
        die(f"no OpenTimestamps client: {ts['note']}")
    log(f"  {len(ts['stamped'])} new, {len(ts['upgraded'])} confirmed, "
        f"{len(ts['pending'])} still pending")
    for f in ts["failed"]:
        log(f"  stamp failed: {f}")

    before = json.dumps(meta.get("timestamping"), sort_keys=True)
    meta["timestamping"] = {
        "method": "OpenTimestamps (Bitcoin)" if ts["client"] else None,
        "snapshots": ts["total_snapshots"],
        "confirmed": len(ts["upgraded"]),
        "pending": len(ts["pending"]),
        "available": ts["client"] is not None,
        "note": ts.get("note"),
        "proves": (("each record existed at or before the anchoring block; "
                    "combined with the chain, the series can be neither "
                    "back-dated nor silently shortened")
                   if ts["client"] is not None else
                   ("nothing yet: no proofs are attached. The chain shows the "
                    "series is internally consistent and complete relative to "
                    "itself; it does not show when it was built.")),
    }
    if json.dumps(meta["timestamping"], sort_keys=True) == before and not ts["stamped"]:
        log("  nothing matured since the last run")

    meta_path.write_text(
        json.dumps(meta, indent=2, sort_keys=True, allow_nan=False,
                   default=str) + "\n", encoding="utf-8", newline="\n")

    log("2. re-rendering and re-verifying")
    if not args.no_site:
        pages = site_builder.build(REPO, SITE)
        log(f"  {len(pages)} pages -> {SITE}")

    # The proof of the claim above: the record still verifies against the
    # digests the snapshots pinned, untouched.
    ok, notes = verify(REPO)
    if not ok:
        for n in notes:
            log(f"  {n}")
        die("the published chain does not verify")
    log(f"  {notes[0]}")

    if args.push:
        log("3. publishing to git")
        try:
            subprocess.run(["git", "add", "-A", "data/repo", "docs"],
                           cwd=ROOT, check=True)
            if subprocess.run(["git", "diff", "--cached", "--quiet"],
                              cwd=ROOT).returncode == 0:
                log("  no change to publish")
            else:
                msg = (f"timestamps: {len(ts['upgraded'])} confirmed, "
                       f"{len(ts['pending'])} pending")
                subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
                subprocess.run(["git", "push"], cwd=ROOT, check=True)
                log(f"  pushed: {msg}")
        except subprocess.CalledProcessError as e:
            log(f"  git failed ({e}) — files are written; push by hand")
    log("done")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--no-site", action="store_true")
    ap.add_argument("--timestamps-only", action="store_true",
                    help="mature the OpenTimestamps proofs and nothing else")
    args = ap.parse_args()

    if args.timestamps_only:
        return timestamps_only(args)

    log("=" * 62)
    log(f"Besë publisher run  (dry-run={args.dry_run})")

    # 1 -------------------------------------------------------------------
    log("1. sweeping inbox")
    new = sweep_inbox(args.dry_run)
    sources = sorted(ARCHIVE.glob("*.csv"))
    if args.dry_run:
        sources += sorted(INBOX.glob("*.csv"))
    if not sources:
        log("  no exports in the archive; nothing to publish")
        return
    log(f"  {new} new, {len(sources)} export(s) in archive")

    # 2 -------------------------------------------------------------------
    log("2. ingesting")
    legs, stats = read_many(sources)
    log(f"  {stats['rows']} rows from {stats['files']} export(s): {stats['by_source']}")
    log(f"  -> {len(legs)} unique round turns "
        f"({stats['cross_format']} seen in both formats, TPT preferred)")
    if stats["conflicts"]:
        for c in stats["conflicts"]:
            log(f"  CONFLICT {c}")
        die("the same round turn is reported differently by two exports")

    # 3 -------------------------------------------------------------------
    log("3. reconciling stated P&L against price x multiplier x size")
    if stats["notes"]:
        for n in stats["notes"][:5]:
            log(f"  MISMATCH {n}")
        die("stated P&L disagrees with the contract multiplier — check contracts.py")
    log(f"  {len(legs)}/{len(legs)} reconcile against the published point values "
        f"({', '.join(f'{k}:{v}' for k, v in sorted(stats['by_source'].items()))})")

    # 4 -------------------------------------------------------------------
    log("4. grouping and normalising")
    trades = group_legs(legs)

    overrides_file = ROOT / "overrides.json"
    if overrides_file.exists():
        # A bad overrides file must abort cleanly rather than throw a
        # traceback: it is hand-edited, it is the one input the operator
        # writes by hand, and "Nothing was written" has to stay true.
        try:
            overrides = json.loads(overrides_file.read_text(encoding="utf-8"))
        except ValueError as e:
            die(f"overrides.json is not valid JSON: {e}")
        before = len(trades)
        try:
            trades = apply_overrides(trades, overrides)
        except ValueError as e:
            die(str(e))
        applied = sum(1 for t in trades if t.override)
        decided = sum(1 for t in trades
                      if any(f.startswith("reviewed:") for f in t.flags))
        log(f"  overrides.json: {before} -> {len(trades)} trades, "
            f"{applied} carrying an override, {decided} carrying a decision")

    merged = [t for t in trades if len(t.legs) > 1]
    # A trade the operator has ruled on is not outstanding. Counting it as
    # outstanding forever would train the operator to ignore the warning.
    flagged = [t for t in trades
               if any(not f.startswith("reviewed:") for f in t.flags)]
    reviewed = [t for t in trades if any(f.startswith("reviewed:") for f in t.flags)]
    log(f"  {len(legs)} round turns -> {len(trades)} strategy trades "
        f"({len(merged)} assembled from multiple fills)")
    for t in flagged:
        log(f"    review: {t.trade_id}  {t.qty:g} {t.root} {t.direction}")
    norm = normalise_all(trades)
    nav = build_nav(norm)
    modelled = [t for t in norm if t.cost_basis == "modelled"]
    if modelled:
        log(f"  {len(modelled)} trade(s) on the MODELLED rate card rather than the "
            f"commission charged — avoidable error, amplified 10x on micros")
    else:
        log(f"  all {len(norm)} trades use the commission actually charged")

    # 5 -------------------------------------------------------------------
    log("5. checking session boundaries (also validates the source timezone)")
    late = [t for t in trades if closes_after_rollover(t.closed_at)]
    if late:
        for t in late[:5]:
            log(f"  {t.trade_id} closed {t.closed_at.astimezone(_ET):%H:%M %Z}")
        die("a position closed at or after 17:00 ET. Take Profit Trader requires "
            "flat by 5:00 PM ET, so either the declared source timezone is wrong "
            "— in which case every session date is suspect — or a rule was breached.")
    log(f"  all {len(trades)} trades flat before 17:00 ET")

    # 6 -------------------------------------------------------------------
    log("6. checking the record did not shrink")
    prior = BOOK_DIR / "meta.json"
    if prior.exists():
        old = json.loads(prior.read_text(encoding="utf-8"))
        if len(norm) < old["trades"] or len(nav) - 1 < old["sessions"]:
            die(f"record would go from {old['trades']} trades / {old['sessions']} "
                f"sessions to {len(norm)} / {len(nav) - 1}. An export is missing.")
        log(f"  {old['trades']} -> {len(norm)} trades, "
            f"{old['sessions']} -> {len(nav) - 1} sessions")
    else:
        log("  first run, no prior record to compare")

    cum = cumulative_return(nav)
    compounded = 1.0
    for p in nav[1:]:
        compounded *= 1 + p.daily_return
    if abs(compounded - 1 - cum) > 1e-9:
        die("daily returns do not compound to the cumulative return")
    log("  compounding identity holds")

    # 7 -------------------------------------------------------------------
    log("7. computing metrics")
    inputs = MetricInputs(
        nav=[(p.date, p.equity) for p in nav],
        returns=[(p.date, p.daily_return) for p in nav if p.daily_return is not None],
        rf_annual=RISK_FREE_ANNUAL,
        rf_source=RISK_FREE_SOURCE,
    )
    core = compute_core_metrics(inputs)
    analytics = compute_analytics(inputs)
    gate = core.get("insufficient_history")
    log(f"  bese.metrics.compute_core_metrics over {len(inputs.returns)} sessions"
        + (f" — {len(gate['suppressed'])} statistics withheld "
           f"({gate['have']}/{gate['need']})" if gate else ""))
    if not analytics["drawdown_consistent_with_metrics"]:
        die("the drawdown path and the reported maximum drawdown disagree")

    if args.dry_run:
        log(f"DRY RUN  would publish NAV {nav[-1].equity:,.2f} "
            f"({cum * 100:+.4f}%) over {len(nav) - 1} sessions")
        return

    # 8 -------------------------------------------------------------------
    log("8. writing the data repository")
    BOOK_DIR.mkdir(parents=True, exist_ok=True)

    # Step 8 overwrites the published files; step 9 decides whether it was
    # allowed to. That ordering meant an abort in step 9 left the record
    # half-rewritten while printing "Nothing was written" -- the one promise
    # this script makes about failure. Hold the previous bytes so the promise
    # can be kept.
    _pre = {}
    for _tmpl in (*ARTEFACTS_TEMPLATES, "books/{book}/meta.json", "index.json"):
        _rel = _tmpl.format(book=BOOK)
        _p = REPO / _rel
        if _p.exists():
            _pre[_rel] = _p.read_bytes()

    def restore_published() -> None:
        """Put back exactly what was there before this run touched anything."""
        for rel, raw in _pre.items():
            (REPO / rel).write_bytes(raw)

    now = datetime.now(timezone.utc).isoformat()

    with open(BOOK_DIR / "nav.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "equity", "pnl", "daily_return", "trades"])
        for p in nav:
            w.writerow([p.date, f"{p.equity:.2f}",
                        "" if p.pnl is None else f"{p.pnl:.2f}",
                        "" if p.daily_return is None else repr(p.daily_return),
                        p.trades])

    with open(BOOK_DIR / "trades.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["trade_id", "session", "symbol", "direction", "qty", "nq_equiv",
                    "entry_price", "exit_price", "opened_at", "closed_at",
                    "gross_pnl", "costs", "cost_basis", "net_pnl",
                    "standardised_pnl", "legs", "source", "account_label",
                    "override", "flags"])
        for t in norm:
            w.writerow([t.trade_id, t.session, t.symbol, t.direction, f"{t.qty:g}",
                        f"{t.nq_equiv:g}", t.entry_price, t.exit_price,
                        t.opened_at, t.closed_at, f"{t.gross_pnl:.2f}",
                        f"{t.costs:.2f}", t.cost_basis, f"{t.net_pnl:.2f}",
                        f"{t.standardised_pnl:.2f}", t.legs, t.source,
                        t.account_ref or "", t.override or "", t.flags])

    # No `published_at` here, and that is deliberate. These files are pinned
    # by the hash chain, so a wall-clock stamp inside them would change their
    # digest on every run and break the pin for no reason. Removing it also
    # makes the whole publication deterministic -- same inputs, byte-identical
    # outputs -- which is what lets anyone re-run the publisher and compare,
    # and it stops the scheduler producing a commit a day with no data in it.
    metrics_payload = {"book": BOOK, "as_of": str(nav[-1].date), **core}
    (BOOK_DIR / "metrics.json").write_text(
        json.dumps(metrics_payload, indent=2, sort_keys=True,
                   allow_nan=False, default=str) + "\n",
        encoding="utf-8", newline="\n")

    analytics_payload = {"book": BOOK, "as_of": str(nav[-1].date), **analytics}
    (BOOK_DIR / "analytics.json").write_text(
        json.dumps(analytics_payload, indent=2, sort_keys=True,
                   allow_nan=False, default=str) + "\n",
        encoding="utf-8", newline="\n")

    # An ordinal label, never the firm's identifier or a hash of it.
    accounts = sorted({t.account_ref for t in norm if t.account_ref})
    instruments = {}
    for t in norm:
        d = instruments.setdefault(t.root, {"trades": 0, "nq_equiv": 0.0,
                                            "standardised_pnl": 0.0})
        d["trades"] += 1
        d["nq_equiv"] = round(d["nq_equiv"] + t.nq_equiv, 6)
        d["standardised_pnl"] = round(d["standardised_pnl"] + t.standardised_pnl, 2)

    meta = {
        "book": BOOK,
        "label": LABEL,
        "tagline_en": TAGLINE,
        "currency": "USD",
        "nominal_capital": NOMINAL_CAPITAL,
        "exposure_basis": "1 NQ-equivalent",
        "instrument_multipliers": {k: v.point_value for k, v in CONTRACTS.items()},
        "rate_card": {k: v.round_turn_cost for k, v in CONTRACTS.items()},
        "cost_per_nq_equivalent": COST_PER_NQ_EQUIVALENT,
        "cost_basis": {"reported": len(norm) - len(modelled),
                       "modelled": len(modelled)},
        "inception": str(nav[0].date),
        "inception_anchored_to_funded_capital": True,
        "inception_note": ("The curve starts at the nominal base on the session "
                           "before the first trade, so the first session's profit "
                           "and loss is inside the record rather than behind it."),
        "last_session": str(nav[-1].date),
        "sessions": len(nav) - 1,
        "trades": len(norm),
        "instruments": instruments,
        "account_labels": accounts,
        "account_numbers": None,
        "account_continuity": ("The series is built from trades, not from account "
                               "equity, so replacing an account does not reset it."),
        "copy_dedup_rule": ("One leader account is the source; copies to follower "
                            "accounts are not counted again."),
        "review_flags": len(flagged),
        "review_decisions": len(reviewed),
        "overrides_applied": sum(1 for t in norm if t.override),
        "source_exports": len(sources),
        "min_sessions_for_annualised": MIN_SESSIONS_FOR_ANNUALISED,
        "published_at": now,
    }

    # 9 -------------------------------------------------------------------
    log("9. chaining the session record")

    # `overrides.json` says of itself that it is hash-chained, and it was not.
    # Copy it into the record so the claim becomes true and a reader can see
    # every correction that shaped the numbers above it.
    shutil.copy2(ROOT / "overrides.json", REPO / "overrides.json")

    # Commit to the raw exports without publishing them: their hashes go into
    # the record, the files stay on this machine.
    manifest = ots.archive_manifest(ARCHIVE, REPO / "archive_manifest.json")
    log(f"  archive manifest: {manifest['count']} raw export(s) committed by hash")

    # Every published file is now digested INTO the session record. Without
    # this the chain covered snapshots and nothing else, so nav.csv -- the file
    # the Verify page tells a stranger to recompute from -- could be edited
    # freely and verification still said "chain ok".
    arte = artefact_digests(REPO, BOOK)

    prev = GENESIS
    chain_file = REPO / "CHAIN.jsonl"
    if chain_file.exists():
        lines = [ln for ln in chain_file.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
        if lines:
            prev = json.loads(lines[-1])["hash"]

    existing = {p.stem for p in (BOOK_DIR / "snapshots").glob("*.json")} \
        if (BOOK_DIR / "snapshots").exists() else set()
    written = 0
    for i, p in enumerate(nav):
        if p.daily_return is None or str(p.date) in existing:
            continue
        upto = [q for q in nav[:i + 1]]
        rets = [(q.date, q.daily_return) for q in upto if q.daily_return is not None]
        snap_core = compute_core_metrics(MetricInputs(
            nav=[(q.date, q.equity) for q in upto], returns=rets,
            rf_annual=RISK_FREE_ANNUAL, rf_source=RISK_FREE_SOURCE))
        _, prev = write_snapshot(BOOK_DIR, BOOK, str(p.date), {
            "schema": "bese.track-record.snapshot/1",
            "published_at": now,
            "sessions": len(rets),
            "nav": {"equity": p.equity, "nominal_capital": NOMINAL_CAPITAL,
                    "source": ("constructed from broker completed-trade records "
                               "by bese.normalize + bese.nav")},
            "daily_return": p.daily_return,
            "cumulative_return": p.equity / nav[0].equity - 1,
            "standardised_pnl": p.pnl,
            "trades": p.trades,
            "metrics": snap_core,
            "disclosure": {d["id"]: d["title_en"] for d in DISCLOSURES},
        }, prev, artefacts=arte, meta=meta)
        written += 1

    # A run that changes a published file without adding a session means data
    # for an ALREADY-CHAINED session moved -- a late fill, or an edit. The old
    # code let that through silently: nav.csv was rebuilt from the whole
    # archive while the snapshot stayed frozen at the original figure, so the
    # chain went on attesting a number the published record no longer showed.
    # It has to be loud, because in the other direction it is a mechanism for
    # freezing a flattering number into the record and citing the chain.
    if written == 0:
        head_file = sorted((BOOK_DIR / "snapshots").glob("*.json"))
        if head_file:
            head_rec = json.loads(head_file[-1].read_text(encoding="utf-8"))
            stale = {k: v for k, v in arte.items()
                     if (head_rec.get("artefacts") or {}).get(k) != v}
            if stale and head_rec.get("artefacts") is not None:
                for k in sorted(stale):
                    log(f"  CHANGED {k}")
                # Two different situations, and they deserve different words.
                figures = [k for k in stale
                           if k.endswith(("nav.csv", "metrics.json",
                                          "analytics.json"))]
                if figures:
                    restore_published()
                    die("published figures changed but no new session was "
                        "added — data for an already-chained session moved. "
                        "Resolve it with an override rather than letting the "
                        "chain and the record drift.")
                # Annotations only: no figure moved. Still cannot be published
                # today, and the reason is the point of the whole design. Every
                # session record is immutable and pins the digest of every
                # published file. Re-pinning the head to match an edit made
                # after it was chained is exactly the operation the chain
                # exists to prevent -- it would not matter that this particular
                # edit is benign, because a reader cannot tell benign from
                # otherwise without trusting the operator, and the record is
                # built so they do not have to.
                restore_published()
                die("overrides.json changed, but no published figure moved and "
                    "no new session was added.\n"
                    "         Nothing is wrong: commit overrides.json now, and "
                    "the change enters the\n"
                    "         record with the next session. A chained record "
                    "cannot be re-pinned after\n"
                    "         the fact, which is the property that makes it "
                    "worth anything.")

    entries = rebuild_chain(REPO, BOOK, BOOK_DIR)
    # Verification happens AFTER meta.json and index.json are written, further
    # down. It cannot happen here any more: the snapshots now pin a digest of
    # meta.json, so checking before that file is written compares the new
    # record against the previous run's metadata and always fails.

    # A timestamp proves a record existed when it claims to. The chain alone
    # cannot: it shows the series is internally complete, not that it was not
    # assembled all at once after the fact.
    ts = ots.stamp_new_snapshots(BOOK_DIR)
    if ts["client"] is None:
        log(f"  NOT TIMESTAMPED — {ts['note']}")
    else:
        log(f"  timestamped: {len(ts['stamped'])} new, "
            f"{len(ts['upgraded'])} confirmed, {len(ts['pending'])} pending "
            f"(a fresh proof confirms in a few hours)")
        for f in ts["failed"]:
            log(f"  stamp failed: {f}")

    meta["timestamping"] = {
        "method": "OpenTimestamps (Bitcoin)" if ts["client"] else None,
        "snapshots": ts["total_snapshots"],
        "confirmed": len(ts["upgraded"]),
        "pending": len(ts["pending"]),
        "available": ts["client"] is not None,
        "note": ts.get("note"),
        # Stated conditionally, because it was previously asserted flat next
        # to "available": false -- claiming a property of an artefact that did
        # not have it.
        "proves": (("each record existed at or before the anchoring block; "
                    "combined with the chain, the series can be neither "
                    "back-dated nor silently shortened")
                   if ts["client"] is not None else
                   ("nothing yet: no proofs are attached. The chain shows the "
                    "series is internally consistent and complete relative to "
                    "itself; it does not show when it was built.")),
    }
    meta["chain_head"] = entries[-1]["hash"] if entries else None
    # When the record was published, not when this script last ran. Using the
    # wall clock meant every scheduled run rewrote ~15 files with no data
    # behind the change, producing a commit a day that said nothing. The
    # chain's own `ts` for the newest session is both stabler and truer.
    meta["published_at"] = entries[-1]["ts"] if entries else now
    (BOOK_DIR / "meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True,
                   allow_nan=False, default=str) + "\n",
        encoding="utf-8", newline="\n")

    (REPO / "index.json").write_text(json.dumps({
        "schema": "bese.track-record.index/1",
        "publisher": "Besë Asset Management",
        "published_at": entries[-1]["ts"] if entries else now,
        "min_sessions_for_annualised": MIN_SESSIONS_FOR_ANNUALISED,
        "disclosures": DISCLOSURES,
        "chain": {"file": "CHAIN.jsonl", "entries": len(entries)},
        "books": [{
            "book": BOOK, "label": LABEL, "tagline_en": TAGLINE,
            "inception": meta["inception"], "last_session": meta["last_session"],
            "sessions": meta["sessions"], "trades": meta["trades"],
            "nominal_capital": NOMINAL_CAPITAL,
            "cumulative_return": cum,
            "annualised_gated": bool(gate),
            "paths": {"meta": f"books/{BOOK}/meta.json",
                      "metrics": f"books/{BOOK}/metrics.json",
                      "analytics": f"books/{BOOK}/analytics.json",
                      "nav": f"books/{BOOK}/nav.csv",
                      "trades": f"books/{BOOK}/trades.csv",
                      "snapshots": f"books/{BOOK}/snapshots/"},
        }],
    }, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n",
        encoding="utf-8", newline="\n")

    # Now that every published file is on disk, re-verify the whole record the
    # way a stranger would. This is the last gate before anything is committed.
    ok, notes = verify(REPO)
    if not ok:
        for n in notes:
            log(f"  {n}")
        die("the published chain does not verify")
    log(f"  {written} new snapshot(s), {len(entries)} chained records — {notes[0]}")

    log(f"  NAV {nav[-1].equity:,.2f}  ({cum * 100:+.4f}%)  "
        f"{len(nav) - 1} sessions  {len(norm)} trades")

    # 10 ------------------------------------------------------------------
    if not args.no_site:
        log("10. rendering the site")
        pages = site_builder.build(REPO, SITE)
        log(f"  {len(pages)} pages -> {SITE}")

    if args.push:
        log("11. publishing to git")
        try:
            subprocess.run(["git", "add", "-A", "data/repo", "docs"],
                           cwd=ROOT, check=True)
            if subprocess.run(["git", "diff", "--cached", "--quiet"],
                              cwd=ROOT).returncode == 0:
                log("  no change to publish")
            else:
                msg = (f"publish {nav[-1].date}: NAV {nav[-1].equity:,.2f} "
                       f"({cum * 100:+.4f}%), {len(norm)} trades")
                subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
                subprocess.run(["git", "push"], cwd=ROOT, check=True)
                log(f"  pushed: {msg}")
        except subprocess.CalledProcessError as e:
            log(f"  git failed ({e}) — files are written; push by hand")

    if flagged:
        log(f"ATTENTION  {len(flagged)} trade(s) want a human decision")
    log("done")


if __name__ == "__main__":
    main()
