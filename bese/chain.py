"""Immutable, hash-chained session records.

Ported from RVB's design (VERIFY.md) without change, because the design is
right and because a reader who has checked one should be able to check the
other with the same commands.

Three properties, and the second is the one that matters:

  1. Each snapshot hashes its own content — SHA-256 of the record's canonical
     JSON with the `hash` field removed. Change a published number and it
     fails.
  2. Each snapshot carries the previous session's hash. A timestamp proves a
     file existed; only the chain proves the SERIES is complete. Selective
     omission — quietly dropping a losing day — is the failure mode that
     actually matters for a track record, and it cannot be done later without
     breaking every record after it.
  3. `CHAIN.jsonl` records both hashes plus the bytes-on-disk digest,
     append-only, one line per snapshot.

For Besë this carries more weight than it does for RVB. Their NAV comes from a
broker endpoint, so it is attested independently of the chain. Besë's NAV is
constructed here, which means the chain plus the open metric code is the whole
of the evidence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def canonical(payload: dict) -> str:
    """The exact serialisation the hash is taken over.

    Byte-identical to RVB's, so the verifier published in their VERIFY.md
    validates a Besë snapshot unmodified.
    """
    return json.dumps(payload, sort_keys=True, indent=2,
                      ensure_ascii=False, default=str) + "\n"


def record_hash(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "hash"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def write_snapshot(book_dir: Path, book: str, session: str,
                   payload: dict, prev_hash: str) -> tuple[Path, str]:
    """Write one immutable session record. Never rewrites an existing file."""
    snaps = book_dir / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    path = snaps / f"{session}.json"

    body = dict(payload)
    body["book"] = book
    body["session_date"] = session
    body["prev_hash"] = prev_hash
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
        if rec["prev_hash"] != prev:
            raise ValueError(f"{path.name}: chain broken — expected prev_hash {prev}")
        if record_hash(rec) != rec["hash"]:
            raise ValueError(f"{path.name}: content does not match its own hash")
        rel = str(path.relative_to(root)).replace("\\", "/")
        entries.append({
            "book": book,
            "file": rel,
            "hash": rec["hash"],
            "prev_hash": rec["prev_hash"],
            "session_date": rec["session_date"],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "ts": known_ts.get(rel, now),
        })
        prev = rec["hash"]

    chain_file.write_text(
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in entries),
        encoding="utf-8", newline="\n")
    return entries


def verify(root: Path) -> tuple[bool, list[str]]:
    """Independent re-verification — the check a stranger would run."""
    problems: list[str] = []
    chain_file = root / "CHAIN.jsonl"
    if not chain_file.exists():
        return False, ["CHAIN.jsonl is missing"]

    prev: dict[str, str] = {}
    count = 0
    for line in chain_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        count += 1
        e = json.loads(line)
        # The chain names its own files, and a chain is exactly the thing a
        # stranger is invited to run this verifier against. A hostile entry
        # ("../../.ssh/id_rsa", or an absolute path) must not be able to make
        # the verifier read outside the record it is checking.
        p = (root / e["file"]).resolve()
        try:
            p.relative_to(root.resolve())
        except ValueError:
            problems.append(f"{e['file']}: path escapes the record directory — refusing to read")
            continue
        if not p.exists():
            problems.append(f"{e['file']}: referenced by the chain but missing")
            continue
        raw = p.read_bytes()
        if hashlib.sha256(raw).hexdigest() != e["sha256"]:
            problems.append(f"{e['file']}: bytes on disk do not match the chain")
        rec = json.loads(raw.decode("utf-8"))
        if record_hash(rec) != rec["hash"]:
            problems.append(f"{e['file']}: content does not match its own hash")
        if rec["prev_hash"] != prev.get(e["book"], GENESIS):
            problems.append(f"{e['file']}: prev_hash does not follow the previous session")
        prev[e["book"]] = rec["hash"]

    return (not problems), problems or [f"{count} records verified"]
