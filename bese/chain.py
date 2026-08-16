"""Immutable, hash-chained session records.

Ported from RVB's design (VERIFY.md), then extended after an audit found that
the original — theirs and ours — proved considerably less than the surrounding
prose claimed.

Five properties. The first three are the original design; the last two are the
audit fixes, and they are the ones that make the first three worth anything.

  1. Each snapshot hashes its own content — SHA-256 of the record's canonical
     JSON with the `hash` field removed. Change a published number and it
     fails.
  2. Each snapshot carries the previous session's hash, so the SERIES is
     covered and not merely each record. Selective omission — quietly dropping
     a losing day — is the failure mode that actually matters for a track
     record.
  3. `CHAIN.jsonl` records both hashes plus the bytes-on-disk digest,
     append-only, one line per snapshot.

  4. **Each snapshot pins the digest of every other published file.** Property
     1 covers the snapshot and nothing else, which meant `nav.csv`,
     `trades.csv`, `metrics.json` and the rest sat outside the chain entirely —
     and `nav.csv` is the file the Verify page tells a stranger to recompute
     from. A losing day could be flipped to a winner, or six of thirteen
     trades deleted, and verification still reported "chain ok". Now the head
     snapshot's `artefacts` map is checked against the bytes on disk.

  5. **Verification enumerates the snapshot directory** instead of trusting
     `CHAIN.jsonl` to list its own contents, binds each snapshot's filename to
     the `session_date` inside it, and requires session dates to increase.
     Previously: deleting the last two lines of `CHAIN.jsonl` silently removed
     the last two sessions and still verified — and a bad streak is always at
     the tail. A fabricated back-dated session could be appended under any
     filename and verified without recomputing a single existing hash.

What this still does not prove — stated here because the gap is the whole
reason `stamp.py` exists — is WHEN the chain was built. Every hash above is
computable by anyone holding the inputs, so a forger who regenerates the entire
record from scratch produces something internally perfect. Only an external
timestamp (OpenTimestamps, anchoring into Bitcoin) closes that, and until those
proofs are attached the chain shows the record is internally consistent, not
that it is old.

For Besë this carries more weight than it does for RVB. Their NAV comes from a
broker endpoint, so it is attested independently of the chain. Besë's NAV is
constructed here, which means the chain plus the open metric code is the whole
of the evidence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

GENESIS = "0" * 64

#: Files whose digests every snapshot pins, byte for byte. Relative to the
#: record root. `meta.json` and `index.json` cannot be pinned this way -- both
#: carry `chain_head`, derived from the snapshot that would be pinning them,
#: and a hash cannot cover a value computed from itself. They are covered
#: instead by `meta_digest()` below, which hashes everything in them EXCEPT the
#: two derived fields. Without that, `"trades": 999` in meta.json was a
#: headline figure on the site that nothing checked.
ARTEFACTS = (
    "books/{book}/nav.csv",
    "books/{book}/trades.csv",
    "books/{book}/metrics.json",
    "books/{book}/analytics.json",
    "archive_manifest.json",
    "overrides.json",
)


def canonical(payload: dict) -> str:
    """The exact serialisation the hash is taken over.

    `allow_nan=False` matters more than it looks. Python emits bare `NaN` and
    `Infinity`, which RFC 8259 does not permit: a snapshot containing either is
    readable by Python's own lenient parser and rejected by every conformant
    one, so a verifier written in Go, Rust or JavaScript could not read the
    record at all while `bese.verify` cheerfully reported "chain ok". Failing
    at write time is the only place this can be caught honestly.
    """
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False,
                      allow_nan=False, default=str) + "\n"


def record_hash(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "hash"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artefact_digests(root: Path, book: str) -> dict:
    """Digest every published file that is not itself a snapshot.

    A missing file is recorded as `null` rather than skipped. Skipping would
    let a forger delete a file to remove it from the chain's coverage, which is
    the same class of bug as trusting CHAIN.jsonl to enumerate itself.
    """
    out: dict = {}
    for tmpl in ARTEFACTS:
        rel = tmpl.format(book=book)
        p = root / rel
        out[rel] = sha256_file(p) if p.exists() else None
    return out


#: Fields excluded from `meta_digest` because they are derived from the chain
#: itself, or from when it was written.
META_DERIVED = ("chain_head", "published_at", "timestamping")


def meta_digest(meta: dict) -> str:
    """Hash a meta payload with its chain-derived fields removed."""
    return hashlib.sha256(
        canonical({k: v for k, v in meta.items() if k not in META_DERIVED})
        .encode("utf-8")).hexdigest()


def write_snapshot(book_dir: Path, book: str, session: str, payload: dict,
                   prev_hash: str, artefacts: dict | None = None,
                   meta: dict | None = None) -> tuple[Path, str]:
    """Write one immutable session record. Never rewrites an existing file."""
    snaps = book_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    path = snaps / f"{session}.json"

    body = dict(payload)
    body["book"] = book
    body["session_date"] = session
    body["prev_hash"] = prev_hash
    if artefacts is not None:
        body["artefacts"] = artefacts
    if meta is not None:
        body["meta_digest"] = meta_digest(meta)
    body["hash"] = record_hash(body)

    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("hash") == body["hash"]:
            return path, body["hash"]          # identical rebuild, no-op
        raise ValueError(
            f"{path.name} already exists with a different hash. A published "
            f"session is immutable; correct it with an override, not an edit.")

    path.write_text(canonical(body), encoding="utf-8", newline="\n")
    return path, body["hash"]


def _snapshot_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("books/*/snapshots") if p.is_dir())


def rebuild_chain(root: Path, book: str, book_dir: Path) -> list[dict]:
    """Regenerate CHAIN.jsonl from the snapshots on disk, and verify it.

    The `ts` of an existing entry is PRESERVED. It records when that session
    was first published, and re-stamping it on every run would be two kinds of
    wrong: it churns the file so every publish shows a diff in lines nothing
    happened to, and it silently rewrites the claimed publication time of
    history. A chain whose timestamps move is not a chain.
    """
    entries: list[dict] = []
    prev = GENESIS
    prev_date: date | None = None
    now = datetime.now(timezone.utc).isoformat()

    known_ts: dict[str, str] = {}
    chain_file = root / "CHAIN.jsonl"
    if chain_file.exists():
        for line in chain_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                known_ts[e["file"]] = e["ts"]

    for path in sorted((book_dir / "snapshots").glob("*.json")):
        raw = path.read_bytes()
        rec = json.loads(raw.decode("utf-8"))

        # The filename is what orders the chain, so it must not be free to
        # disagree with the date inside. Without this, a record claiming any
        # session_date at all could be dropped in under a filename that sorts
        # last and appended without recomputing one existing hash.
        if path.stem != rec.get("session_date"):
            raise ValueError(
                f"{path.name}: filename does not match session_date "
                f"{rec.get('session_date')!r}")
        this_date = date.fromisoformat(rec["session_date"])
        if prev_date is not None and this_date <= prev_date:
            raise ValueError(
                f"{path.name}: session dates must increase along the chain "
                f"(previous was {prev_date})")
        if rec["prev_hash"] != prev:
            raise ValueError(f"{path.name}: chain broken — expected prev_hash {prev}")
        if record_hash(rec) != rec["hash"]:
            raise ValueError(f"{path.name}: content does not match its own hash")

        rel = str(path.relative_to(root)).replace("\\", "/")
        entries.append({
            "book": rec["book"],
            "file": rel,
            "hash": rec["hash"],
            "prev_hash": rec["prev_hash"],
            "session_date": rec["session_date"],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "ts": known_ts.get(rel, now),
        })
        prev = rec["hash"]
        prev_date = this_date

    chain_file.write_text(
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in entries),
        encoding="utf-8", newline="\n")
    return entries


def verify(root: Path) -> tuple[bool, list[str]]:
    """Independent re-verification — the check a stranger would run."""
    problems: list[str] = []
    root = Path(root)
    chain_file = root / "CHAIN.jsonl"
    if not chain_file.exists():
        return False, ["CHAIN.jsonl is missing"]

    prev: dict[str, str] = {}
    prev_date: dict[str, date] = {}
    seen: set[Path] = set()
    head = None
    count = 0

    for lineno, line in enumerate(
            chain_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        count += 1

        # A malformed chain is a verification FAILURE, not a traceback. The
        # audience for this function is a stranger checking the record; handing
        # them a Python stack trace instead of a verdict is its own small
        # failure of the thing the record is for.
        try:
            e = json.loads(line)
            file_rel, book = str(e["file"]), str(e["book"])
        except (ValueError, KeyError, TypeError) as exc:
            problems.append(f"CHAIN.jsonl line {lineno}: unreadable — {exc}")
            continue

        # The chain names its own files, and a chain is exactly the thing a
        # stranger is invited to run this verifier against. A hostile entry
        # ("../../.ssh/id_rsa", or an absolute path) must not be able to make
        # the verifier read outside the record it is checking.
        p = (root / file_rel).resolve()
        try:
            p.relative_to(root.resolve())
        except ValueError:
            problems.append(f"{file_rel}: path escapes the record directory — refusing to read")
            continue
        if not p.exists():
            problems.append(f"{file_rel}: referenced by the chain but missing")
            continue
        seen.add(p)

        raw = p.read_bytes()
        if hashlib.sha256(raw).hexdigest() != e.get("sha256"):
            problems.append(f"{file_rel}: bytes on disk do not match the chain")
        try:
            rec = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            problems.append(f"{file_rel}: not readable as JSON — {exc}")
            continue

        if record_hash(rec) != rec.get("hash"):
            problems.append(f"{file_rel}: content does not match its own hash")

        # Every column of the chain must agree with the record it describes.
        # Previously only `sha256` was checked, so the chain a reader is
        # invited to read could state any hash, date or book it liked and still
        # verify.
        for col in ("hash", "prev_hash", "session_date", "book"):
            if e.get(col) != rec.get(col):
                problems.append(
                    f"{file_rel}: chain says {col}={e.get(col)!r}, "
                    f"record says {rec.get(col)!r}")

        if Path(file_rel).stem != rec.get("session_date"):
            problems.append(
                f"{file_rel}: filename does not match session_date "
                f"{rec.get('session_date')!r}")

        if rec.get("prev_hash") != prev.get(book, GENESIS):
            problems.append(f"{file_rel}: prev_hash does not follow the previous session")

        try:
            d = date.fromisoformat(str(rec.get("session_date")))
            if book in prev_date and d <= prev_date[book]:
                problems.append(
                    f"{file_rel}: session date {d} does not follow {prev_date[book]}")
            prev_date[book] = d
        except ValueError:
            problems.append(f"{file_rel}: session_date is not a date")

        prev[book] = rec.get("hash")
        head = (rec, file_rel)

    # A chain that lists its own contents cannot detect being shortened. Walk
    # the directory instead: deleting the last two lines of CHAIN.jsonl used to
    # drop the last two sessions silently, and losses cluster at the tail.
    for d in _snapshot_dirs(root):
        for p in sorted(d.glob("*.json")):
            if p.resolve() not in seen:
                rel = str(p.relative_to(root)).replace("\\", "/")
                problems.append(f"{rel}: on disk but absent from the chain")

    # And the files the chain did not used to cover at all.
    if head is not None:
        rec, file_rel = head
        arte = rec.get("artefacts")
        if arte is None:
            problems.append(
                f"{file_rel}: head snapshot pins no artefact digests, so "
                f"nav.csv, trades.csv and the metric files are unprotected")
        else:
            for rel, want in sorted(arte.items()):
                q = (root / rel).resolve()
                try:
                    q.relative_to(root.resolve())
                except ValueError:
                    problems.append(f"{rel}: artefact path escapes the record directory")
                    continue
                if want is None:
                    if q.exists():
                        problems.append(f"{rel}: present, but the chain records it as absent")
                elif not q.exists():
                    problems.append(f"{rel}: pinned by the chain but missing")
                elif sha256_file(q) != want:
                    problems.append(f"{rel}: does not match the digest pinned by the record")

        want_meta = rec.get("meta_digest")
        book = rec.get("book")
        mp = root / f"books/{book}/meta.json"
        if want_meta is None:
            problems.append(f"{file_rel}: head snapshot pins no meta digest")
        elif not mp.exists():
            problems.append(f"books/{book}/meta.json: pinned by the record but missing")
        else:
            try:
                got = meta_digest(json.loads(mp.read_text(encoding="utf-8")))
            except ValueError as exc:
                problems.append(f"books/{book}/meta.json: not readable as JSON — {exc}")
                got = None
            if got is not None and got != want_meta:
                problems.append(
                    f"books/{book}/meta.json: does not match the digest pinned "
                    f"by the record (chain_head and published_at excluded)")

    if not problems:
        n_art = len(head[0].get("artefacts") or {}) if head else 0
        return True, [f"{count} records verified, {n_art} published files pinned"]
    return False, problems
