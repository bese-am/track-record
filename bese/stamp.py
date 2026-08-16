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
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

CALENDAR_TIMEOUT = 90


#: Where proofs are SUBMITTED. These are aggregators: load balancers that take
#: a digest and hand it to whichever calendar server is behind them.
AGGREGATORS = (
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
    "https://ots.btc.catallaxy.com",
)

#: Which calendars an UPGRADE may be fetched from -- a different list, and
#: conflating the two is why proofs sat pending indefinitely. A pending
#: attestation names the calendar server that actually holds the commitment
#: (alice.btc.calendar.opentimestamps.org), never the aggregator it was
#: submitted through (a.pool.opentimestamps.org). Checking the attestation's
#: URI against the aggregator list therefore matched nothing, every upgrade was
#: skipped, and the record reported "pending" forever with no error to show for
#: it -- the worst shape of bug, one whose symptom is patience.
#:
#: The check itself is not optional: an attestation names its own calendar, so
#: without a whitelist a hostile proof could point the upgrade at any URL it
#: liked. Use the library's own glob whitelist rather than a hand-rolled one.
#: A proof is worth writing once this many calendars have committed to it.
MIN_CALENDARS = 2


def _ots_lib():
    """The OpenTimestamps library, or None.

    Deliberately NOT `otsclient`. The reference CLI is a thin wrapper around
    this library, but `otsclient.cmds` imports `bitcoin.rpc` at module level
    for its *verify* path, which drags in `bitcoin.core.key`, which does:

        ctypes.cdll.LoadLibrary(ctypes.util.find_library('ssl') or ...)

    On Windows that lookup returns None and the import dies before any command
    runs -- so `ots stamp` is unusable there even though stamping involves no
    Bitcoin operations whatsoever. Talking to the library directly skips the
    broken module entirely: submitting a digest to a calendar is an HTTP POST,
    and the proof format is pure serialisation.

    It also removes a subprocess, a PATH lookup and an OpenSSL dependency from
    a job that runs unattended, which is worth having regardless of platform.
    """
    try:
        from opentimestamps.calendar import RemoteCalendar          # noqa: F401
        from opentimestamps.core.op import OpAppend, OpSHA256       # noqa: F401
        from opentimestamps.core.serialize import (                 # noqa: F401
            StreamDeserializationContext, StreamSerializationContext)
        from opentimestamps.core.timestamp import DetachedTimestampFile  # noqa: F401
        import opentimestamps.core.notary as notary                 # noqa: F401
        return True
    except ImportError:
        return False


def _is_confirmed(timestamp) -> bool:
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    return any(isinstance(a, BitcoinBlockHeaderAttestation)
               for _, a in timestamp.all_attestations())


def _native_stamp(path: Path) -> tuple[bool, str]:
    import os
    from opentimestamps.calendar import RemoteCalendar
    from opentimestamps.core.op import OpAppend, OpSHA256
    from opentimestamps.core.serialize import StreamSerializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile

    with open(path, "rb") as fd:
        file_ts = DetachedTimestampFile.from_fd(OpSHA256(), fd)

    # A random nonce before the second hash, exactly as the reference client
    # does it: the calendar learns a digest that reveals nothing about the
    # file, and two files stamped together cannot be linked by their proofs.
    nonce = file_ts.timestamp.ops.add(OpAppend(os.urandom(16)))
    merkle_root = nonce.ops.add(OpSHA256())

    ok, problems = 0, []
    for url in AGGREGATORS:
        try:
            merkle_root.merge(
                RemoteCalendar(url).submit(merkle_root.msg, timeout=CALENDAR_TIMEOUT))
            ok += 1
        except Exception as e:                                  # noqa: BLE001
            problems.append(f"{url.split('//')[-1]}: {e}")

    if ok < MIN_CALENDARS:
        return False, (f"only {ok} of {len(AGGREGATORS)} calendars responded; "
                       f"not writing a proof — " + "; ".join(problems))

    proof = path.with_suffix(path.suffix + ".ots")
    with open(proof, "xb") as fd:
        file_ts.serialize(StreamSerializationContext(fd))
    return True, f"{ok}/{len(CALENDARS)} calendars committed"


def _native_upgrade(proof: Path) -> tuple[bool, str]:
    """Ask the calendars to replace pending commitments with Bitcoin ones."""
    from opentimestamps.calendar import DEFAULT_CALENDAR_WHITELIST, RemoteCalendar
    from opentimestamps.core.notary import PendingAttestation
    from opentimestamps.core.serialize import (
        StreamDeserializationContext, StreamSerializationContext)
    from opentimestamps.core.timestamp import DetachedTimestampFile

    with open(proof, "rb") as fd:
        file_ts = DetachedTimestampFile.deserialize(StreamDeserializationContext(fd))
    if _is_confirmed(file_ts.timestamp):
        return True, "Success! Bitcoin attests"

    def directly_verified(stamp):
        if stamp.attestations:
            yield stamp
        else:
            for sub in stamp.ops.values():
                yield from directly_verified(sub)

    before = {a for _, a in file_ts.timestamp.all_attestations()}
    for sub in list(directly_verified(file_ts.timestamp)):
        for att in list(sub.attestations):
            if not isinstance(att, PendingAttestation):
                continue
            uri = att.uri.decode() if isinstance(att.uri, bytes) else str(att.uri)
            if not uri.startswith(("http://", "https://")):
                uri = "https://" + uri
            if uri not in DEFAULT_CALENDAR_WHITELIST:
                continue
            try:
                sub.merge(RemoteCalendar(uri).get_timestamp(sub.msg))
            except Exception:                                   # noqa: BLE001
                continue

    after = {a for _, a in file_ts.timestamp.all_attestations()}
    if after != before:
        tmp = proof.with_suffix(proof.suffix + ".tmp")
        with open(tmp, "wb") as fd:
            file_ts.serialize(StreamSerializationContext(fd))
        tmp.replace(proof)

    return (_is_confirmed(file_ts.timestamp),
            "Success! Bitcoin attests" if _is_confirmed(file_ts.timestamp)
            else "still pending confirmation")


def _client() -> list[str] | None:
    """How to invoke the OpenTimestamps client, or None if it is absent.

    `pip install opentimestamps-client` drops `ots.exe` into the per-user
    Scripts directory, which is not on PATH in a default Windows Python
    install -- pip even warns about it and the warning scrolls past. The
    result was a publisher that installed the client successfully and then
    reported "not installed" forever, which is a bad failure because it looks
    like a decision rather than a lookup miss.

    So: PATH first, then the module directly through the interpreter that is
    already running. The second form needs no PATH at all and is what makes
    this work unattended.
    """
    exe = shutil.which("ots")
    if exe:
        return [exe]
    if importlib.util.find_spec("otsclient") is not None:
        return [sys.executable, "-m", "otsclient.ots"]
    return None


def available() -> bool:
    return _ots_lib() or _client() is not None


def _run(args: list[str], timeout: int = CALENDAR_TIMEOUT) -> tuple[bool, str]:
    exe = _client()
    if not exe:
        return False, "ots client not installed"
    try:
        r = subprocess.run([*exe, *args], capture_output=True, text=True,
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
    if _ots_lib():
        try:
            return _native_stamp(path)
        except Exception as e:                                  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
    ok, out = _run(["stamp", str(path)])
    return (ok and proof.exists()), out or "stamped"


def upgrade(proof: Path) -> tuple[bool, str]:
    """Complete a pending proof once the aggregating Bitcoin transaction has
    confirmed. A fresh proof is a commitment to a calendar server and is
    INCOMPLETE, normally for a few hours. Incomplete means 'not yet confirmed',
    not 'invalid' — and saying so is the difference between a caveat and a
    misrepresentation."""
    if _ots_lib():
        try:
            return _native_upgrade(proof)
        except Exception as e:                                  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
    return _run(["upgrade", str(proof)])


def verify(path: Path) -> tuple[bool, str]:
    """Check a proof against the block chain.

    This is the one operation that genuinely needs Bitcoin, so it goes through
    the reference CLI and needs a local Bitcoin Core node (a pruned one is
    fine). That is not an inconvenience to work around -- the point of the
    design is that verification asks no third party to be trusted. The
    publisher never calls this; it is here for whoever is checking the record.
    """
    proof = path.with_suffix(path.suffix + ".ots")
    if not proof.exists():
        return False, "no proof beside this file"
    if _client() is None:
        return False, ("verifying needs the `ots` CLI and a Bitcoin node; "
                       "stamping does not, which is why it works without them")
    return _run(["verify", str(proof)])


def stamp_new_snapshots(book_dir: Path) -> dict:
    """Stamp every snapshot that has no proof, and try to complete the rest."""
    snaps = sorted((book_dir / "snapshots").glob("*.json"))
    out = {"client": ("python-opentimestamps" if _ots_lib()
                      else "opentimestamps-client" if available() else None),
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


def anchors(book_dir: Path) -> dict:
    """{session_date: earliest Bitcoin block} for every snapshot with a proof.

    Read here rather than in the site, because the site computes nothing: it
    renders what the record says. Anchor status is also the one published fact
    that legitimately changes without any data changing -- a proof matures from
    pending to confirmed on Bitcoin's schedule, not the operator's -- which is
    why it lives under `timestamping`, outside the meta digest.
    """
    if not _ots_lib():
        return {}
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    from opentimestamps.core.serialize import StreamDeserializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile

    out = {}
    for proof in sorted((book_dir / "snapshots").glob("*.json.ots")):
        try:
            with open(proof, "rb") as fd:
                fts = DetachedTimestampFile.deserialize(
                    StreamDeserializationContext(fd))
            blocks = [a.height for _, a in fts.timestamp.all_attestations()
                      if isinstance(a, BitcoinBlockHeaderAttestation)]
        except Exception:                                       # noqa: BLE001
            continue
        if blocks:
            out[proof.name.replace(".json.ots", "")] = min(blocks)
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
        # No wall-clock stamp. This file is pinned by the hash chain, so a
        # timestamp inside it would change its digest on every run and break
        # the pin for no reason -- and when the record was published is what
        # CHAIN.jsonl's `ts` and the OpenTimestamps proofs are for.
        "files": files,
        "count": len(files),
    }
    out_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n")
    return manifest
