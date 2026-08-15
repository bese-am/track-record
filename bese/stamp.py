"""Proof that a record existed when it says it did.

First, the thing worth being clear about, because it is easy to claim more
than is true.

**Your trades are already timestamped, and not by you.** The firm's export
carries a millisecond UTC instant for every entry and exit, written by the
exchange and the firm, not by this code. That is third-party attestation of
*when a trade happened*, and nothing here improves on it. No amount of
cryptography makes an operator's own claim about his own fills stronger than
the broker's record of them.

**What is not yet proven is when the RECORD was assembled.** The hash chain
shows the series is complete and unedited relative to itself; it does not stop
someone building the whole chain in one afternoon and presenting it as months
of history. That is the gap this module closes.

An OpenTimestamps proof anchors a file's hash into a Bitcoin block. It proves
the file existed **at or before** that block. It cannot prove the file did not
exist earlier — which is fine, because the useful claim runs the other way:
session N's record was stamped on day N, so it cannot have been rewritten
afterwards to suit what happened next. The chain stops deletion; the timestamp
stops back-dating; neither alone is enough.

**And one thing specific to Besë.** The NAV here is constructed from the raw
exports, so those exports are load-bearing evidence — but they are not
published, because they carry the firm's account identifier. So the manifest
below stamps their HASHES. The raw file stays private today, and if the record
is ever challenged it can be produced and shown to be the very file held on the
day. Publishing a hash costs nothing and forecloses "you edited the source".

Requires the `ots` client (`pip install opentimestamps-client`). Where it is
absent or offline, every payload says so rather than quietly implying a proof
exists.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

CALENDAR_TIMEOUT = 90


def available() -> bool:
    return shutil.which("ots") is not None


def _run(args: list[str], timeout: int = CALENDAR_TIMEOUT) -> tuple[bool, str]:
    exe = shutil.which("ots")
    if not exe:
        return False, "ots client not installed"
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True,
                           timeout=timeout, check=False)
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return False, "calendar servers did not respond in time"
    except OSError as e:                                        # noqa: BLE001
        return False, f"ots could not be run: {e}"


def stamp(path: Path) -> tuple[bool, str]:
    """Create `<path>.ots`. Idempotent: an existing proof is never replaced."""
    proof = path.with_suffix(path.suffix + ".ots")
    if proof.exists():
        return True, "already stamped"
    ok, out = _run(["stamp", str(path)])
    return (ok and proof.exists()), out or "stamped"


def upgrade(proof: Path) -> tuple[bool, str]:
    """Complete a pending proof once the aggregating Bitcoin transaction has
    confirmed. A fresh proof is a commitment to a calendar server and is
    INCOMPLETE, normally for a few hours. Incomplete means 'not yet confirmed',
    not 'invalid' — and saying so is the difference between a caveat and a
    misrepresentation."""
    return _run(["upgrade", str(proof)])


def verify(path: Path) -> tuple[bool, str]:
    proof = path.with_suffix(path.suffix + ".ots")
    if not proof.exists():
        return False, "no proof beside this file"
    return _run(["verify", str(proof)])


def stamp_new_snapshots(book_dir: Path) -> dict:
    """Stamp every snapshot that has no proof, and try to complete the rest."""
    snaps = sorted((book_dir / "snapshots").glob("*.json"))
    out = {"client": "opentimestamps-client" if available() else None,
           "stamped": [], "upgraded": [], "pending": [], "failed": [],
           "total_snapshots": len(snaps)}

    if not available():
        out["note"] = ("ots client not installed — snapshots are chained but NOT "
                       "timestamped. Install opentimestamps-client to close this.")
        return out

    for s in snaps:
        proof = s.with_suffix(s.suffix + ".ots")
        if not proof.exists():
            ok, msg = stamp(s)
            (out["stamped"] if ok else out["failed"]).append(
                s.name if ok else f"{s.name}: {msg}")
            out["pending"].append(s.name)
            continue
        ok, msg = upgrade(proof)
        if ok and "Success" in msg:
            out["upgraded"].append(s.name)
        else:
            out["pending"].append(s.name)
    return out


def archive_manifest(archive_dir: Path, out_path: Path) -> dict:
    """Commit to the raw exports without publishing them.

    Each entry is the SHA-256 of a file that stays on the operator's disk. The
    manifest is committed and stamped, so the exports behind the record are
    fixed in time even though the record does not reveal them.
    """
    files = []
    for f in sorted(archive_dir.glob("*.csv")) if archive_dir.exists() else []:
        raw = f.read_bytes()
        files.append({
            "file": f.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            # Row count only. Not the contents, and nothing identifying.
            "rows": max(raw.decode("utf-8", "replace").count("\n") - 1, 0),
        })

    manifest = {
        "schema": "bese.archive-manifest/1",
        "note": ("SHA-256 of every raw export the published record was built "
                 "from. The exports are NOT published — they carry the firm's "
                 "account identifier — but they are committed to here, so any "
                 "one of them can later be produced and shown to be the file "
                 "held on this date."),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "count": len(files),
    }
    out_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
