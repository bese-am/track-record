"""python3 -m bese.verify [path] — re-verify the published chain from scratch.

With no argument this checks EVERY record tree in the repository and requires
them to be byte-identical to one another. There are two — `data/repo`, which
the publisher writes, and `docs/data`, which the website serves — and until now
this command defaulted to the first while the Verify page pointed a reader at
the second. Nothing compared them, so tampering with the copy the public
actually downloads was invisible to the command offered for catching exactly
that.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from bese.chain import verify

ROOTS = ("data/repo", "docs/data")


def tree_digest(root: Path) -> str:
    """One hash over every file in a record tree, path and content."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).replace("\\", "/").encode())
            h.update(b"\0")
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def main() -> int:
    repo = Path(__file__).parent.parent
    if len(sys.argv) > 1:
        roots = [Path(sys.argv[1])]
    else:
        roots = [repo / r for r in ROOTS if (repo / r / "CHAIN.jsonl").exists()]
        if not roots:
            print("FAIL  no record tree found")
            print("CHAIN DOES NOT VERIFY")
            return 1

    ok = True
    for root in roots:
        rok, notes = verify(root)
        ok = ok and rok
        label = root.name if len(roots) == 1 else str(root.relative_to(repo))
        for n in notes:
            print(("  " if rok else "FAIL  ") + f"[{label}] {n}")

    if len(roots) > 1:
        digests = {str(r.relative_to(repo)): tree_digest(r) for r in roots}
        if len(set(digests.values())) != 1:
            ok = False
            print("FAIL  the published trees differ from each other:")
            for r, d in sorted(digests.items()):
                print(f"      {d[:16]}…  {r}")
        else:
            print(f"  both record trees are byte-identical "
                  f"({next(iter(digests.values()))[:16]}…)")

    print("chain ok" if ok else "CHAIN DOES NOT VERIFY")
    return 0 if ok else 1


# Guarded: importing a module must never run it. Without this, anything that
# imports bese.verify -- a test, a linter, an IDE -- would verify a directory
# and call sys.exit on the way past.
if __name__ == "__main__":
    sys.exit(main())
