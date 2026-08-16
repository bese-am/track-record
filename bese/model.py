"""The one shape every data source is reduced to.

Besë has two possible inputs and they carry different truths:

    Tradovate "Performance" export  -- exact broker fill ids, gross P&L, no
                                       commission, no timezone on timestamps
    Take Profit Trader "completed   -- actual commission charged, net P&L,
    trades" export                     explicit UTC, explicit direction, the
                                       firm's own session label, no fill ids

Neither is a superset of the other, so neither is hard-coded as *the* format.
Both are read into `Leg` and everything downstream sees only that. A source
declares what it knows and leaves the rest `None`; nothing later has to ask
which file a number came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


_ACCOUNT_LABELS: dict = {}


def reset_account_labels() -> None:
    """Labels are assigned per publishing run; tests need a clean slate."""
    _ACCOUNT_LABELS.clear()


def account_ref(account: str | None) -> str | None:
    """A stable, non-identifying reference to an account.

    A HASH of the identifier is not good enough, which is the mistake this
    replaces. The firm issues identifiers of a known shape -- a fixed
    alphabetic prefix and nine digits -- so the whole space is about 10^9
    candidates, and the record names the firm in its own `source` column.
    SHA-256 is fast by design: that space sweeps in minutes on a laptop and
    under a second on a GPU. Publishing the hash of a low-entropy identifier
    publishes the identifier.

    So the published reference carries no preimage at all. Accounts are
    labelled in the order they first appear, which preserves everything the
    record actually claims -- that two trades came from the same account, and
    that the series survived an account being replaced -- and reveals nothing
    else. Trades are sorted by (closed_at, trade_id) before normalisation, so
    the labelling is deterministic across runs.
    """
    if not account:
        return None
    if account not in _ACCOUNT_LABELS:
        _ACCOUNT_LABELS[account] = f"account {len(_ACCOUNT_LABELS) + 1}"
    return _ACCOUNT_LABELS[account]


@dataclass(frozen=True)
class Leg:
    """One broker-matched round turn, from whichever export supplied it."""

    source: str                     # "tpt" | "tradovate"
    leg_id: str                     # stable, source-scoped
    account: str | None
    symbol: str                     # raw, as exported
    root: str                       # NQ | MNQ
    direction: str                  # "long" | "short"
    qty: float
    entry_price: float
    exit_price: float
    opened_at: datetime             # timezone-aware, always
    closed_at: datetime
    gross_pnl: float

    #: Commission actually charged. None means the source does not report it
    #: and the rate card in contracts.py must stand in -- a modelled figure,
    #: and labelled as one in the published record.
    commission: float | None

    #: The firm's own session label, when it publishes one. Preferred over
    #: deriving the session from a timestamp, because it is the authority on
    #: which day it thinks the trade belongs to.
    session_hint: date | None

    #: Keys that bind this leg to others on the same position. Two legs
    #: sharing any key are the same strategy trade. Sources supply whatever
    #: linkage they can prove; the grouper does not care which kind.
    link_keys: tuple[str, ...] = field(default_factory=tuple)

    #: Anything worth surfacing that is not an error.
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_short(self) -> bool:
        return self.direction == "short"

    @property
    def dedupe_key(self) -> tuple:
        """Identity of the underlying round turn, for matching the same trade
        across the two export formats.

        Timestamps are truncated to the second: TPT reports milliseconds and
        Tradovate does not, so full precision would never match. Prices, size,
        contract, direction and both instants together identify a round turn
        far more tightly than is needed -- the point is to be certain, not
        clever.
        """
        return (self.root, self.direction, self.qty,
                self.opened_at.replace(microsecond=0),
                self.closed_at.replace(microsecond=0),
                round(self.entry_price, 6), round(self.exit_price, 6))
