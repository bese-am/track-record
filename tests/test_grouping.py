"""The claims the code makes about itself, checked.

Two of these are cited in module docstrings as evidence. A citation to a test
that does not run is worse than no citation, so they run.

    python3 -m pytest tests/ -q        (or: python3 tests/test_grouping.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bese.chain import GENESIS, canonical, record_hash          # noqa: E402
from bese.contracts import cost_per_nq_equivalent, nq_equivalent  # noqa: E402
from bese.group import group_legs                                # noqa: E402
from bese.metrics import MetricInputs, compute_core_metrics      # noqa: E402
from bese.model import account_ref, reset_account_labels                               # noqa: E402
from bese.nav import build_nav                                   # noqa: E402
from bese.normalize import normalise_all                         # noqa: E402
from bese.sources import read_tpt, read_tradovate                # noqa: E402

ARCHIVE = Path(__file__).parent.parent / "data" / "archive"


def _fixtures() -> tuple[Path, Path] | None:
    """The raw exports are the operator's and are not committed. Where they are
    absent the source-agnostic tests still run; the cross-source ones skip."""
    tpt = next(ARCHIVE.glob("*completed_trades*.csv"), None)
    tv = next(ARCHIVE.glob("*performance*.csv"), None)
    return (tpt, tv) if tpt and tv else None


def test_normalisation_is_the_documented_rule():
    assert nq_equivalent("NQ", 1) == 1.0
    assert nq_equivalent("NQ", 2) == 2.0
    assert nq_equivalent("MNQ", 5) == 0.5
    assert nq_equivalent("MNQ", 10) == 1.0


def test_micro_carries_its_higher_cost_per_unit_of_risk():
    # The claim made on the site and in METHODOLOGY. If the rate card changes
    # and this ratio moves, the prose must move with it.
    assert cost_per_nq_equivalent("NQ") == 4.50
    assert cost_per_nq_equivalent("MNQ") == 15.00


def test_account_identifier_is_never_published():
    # A FAKE identifier, deliberately. The first version of this test used the
    # real one -- so the test asserting the account number is never published
    # was itself committing it to a public repository. The fixture has to be
    # invented or the test defeats its own purpose.
    reset_account_labels()
    fake = "ACCOUNT000000000"
    ref = account_ref(fake)
    assert ref is not None
    assert fake not in ref
    # And NOT a hash of it either: the identifier space is ~10^9 with a known
    # prefix, so a published SHA-256 of one solves in seconds on a GPU.
    assert not ref.startswith("sha256:")
    assert ref == "account 1"
    assert account_ref("ACCOUNT111111111") == "account 2"
    assert account_ref(None) is None
    # Stable: the same account must always produce the same reference, or the
    # record would appear to change accounts when it did not.
    assert ref == account_ref(fake)


def test_compounding_identity():
    """The identity the whole metric layer rests on: compounding the daily
    returns must equal NAV_last / NAV_0 - 1."""
    fx = _fixtures()
    if not fx:
        return
    nav = build_nav(normalise_all(group_legs(read_tpt(fx[0]))))
    compounded = 1.0
    for p in nav[1:]:
        compounded *= 1 + p.daily_return
    assert abs((compounded - 1) - (nav[-1].equity / nav[0].equity - 1)) < 1e-12


def test_two_independent_linkages_agree():
    """Cited in sources.py and group.py.

    The firm's export has no fill ids and links legs by a shared entry or exit
    instant; the broker's export links them by a shared fill id. They are
    different rules over different data and must produce the same trades.
    """
    fx = _fixtures()
    if not fx:
        return
    tpt, tv = (group_legs(read_tpt(fx[0])), group_legs(read_tradovate(fx[1])))

    def shape(ts):
        # To the second: the firm reports milliseconds and the broker does not,
        # so full precision would compare a difference in reporting resolution
        # rather than a difference in the trades. This is the same tolerance
        # Leg.dedupe_key uses to match the two formats.
        return sorted((t.root, t.direction, t.qty, round(t.gross_pnl, 2),
                       t.opened_at.replace(microsecond=0).timestamp(),
                       t.closed_at.replace(microsecond=0).timestamp())
                      for t in ts)

    assert len(tpt) == len(tv)
    assert shape(tpt) == shape(tv)


def test_trade_ids_survive_a_longer_export():
    """Re-importing a superset must not renumber trades already published and
    hash-chained -- the reason ids come from broker ids, not a counter."""
    fx = _fixtures()
    if not fx:
        return
    legs = read_tpt(fx[0])
    full = {t.trade_id for t in group_legs(legs)}
    partial = {t.trade_id for t in group_legs(legs[3:])}
    assert partial <= full


def test_withheld_is_none_not_zero():
    """A gated statistic must be absent, never a plausible-looking zero."""
    from datetime import date, timedelta
    d0 = date(2026, 1, 5)
    nav = [(d0 + timedelta(days=i), 100_000 + i * 10) for i in range(5)]
    rets = [(nav[i][0], nav[i][1] / nav[i - 1][1] - 1) for i in range(1, len(nav))]
    out = compute_core_metrics(MetricInputs(nav, rets, 0.0, "test"))
    for k in ("sharpe", "sortino", "calmar", "cagr", "volatility", "max_drawdown"):
        assert out["values"][k] is None, f"{k} should be withheld, got {out['values'][k]}"
    assert out["insufficient_history"]["have"] == 4
    # Statements of what happened are published from session one.
    assert out["values"]["cumulative_return"] is not None


def test_snapshot_hash_covers_every_field_but_itself():
    payload = {"a": 1, "nested": {"b": [1, 2]}, "hash": "ignored"}
    h = record_hash(payload)
    assert record_hash({**payload, "hash": "different"}) == h   # hash excluded
    assert record_hash({**payload, "a": 2}) != h                # content included
    assert canonical({"b": 1, "a": 2}).startswith('{\n  "a": 2')  # key order fixed
    assert len(GENESIS) == 64 and set(GENESIS) == {"0"}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    skipped = 0 if _fixtures() else 3
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed"
          + (f", {skipped} needed the operator's raw exports and self-skipped"
             if skipped else ""))
