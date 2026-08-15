"""python3 -m bese.verify [path] — re-verify the published chain from scratch."""

from __future__ import annotations

import sys
from pathlib import Path

from bese.chain import verify


def main() -> int:
    root = (Path(sys.argv[1]) if len(sys.argv) > 1
            else Path(__file__).parent.parent / "data" / "repo")
    ok, notes = verify(root)
    for n in notes:
        print(("  " if ok else "FAIL  ") + n)
    print("chain ok" if ok else "CHAIN DOES NOT VERIFY")
    return 0 if ok else 1


# Guarded: importing a module must never run it. Without this, anything that
# imports bese.verify -- a test, a linter, an IDE -- would verify a directory
# and call sys.exit on the way past.
if __name__ == "__main__":
    sys.exit(main())
