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

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime


def account_ref(account: str | None) -> str | None:
    """A stable, non-identifying reference to an account.

    The firm's account identifier is not published. It identifies a real
    account at a real firm, it appears in nothing a reader needs, and a track
    record is a document strangers are invited to scrutinise -- so the
    identifier itself is a liability with no compensating benefit. The hash
    still proves two trades came from the same account, and still changes when
    an account is replaced, which is all the record actually claims.

    Same convention as the system this is modelled on, which publishes
    `account_ref: "sha256:..."` and `account_number: null`.
    """
    if not account:
        return None
    return "sha256:" + hashlib.sha256(account.encode("utf-8")).hexdigest()[:16]


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
