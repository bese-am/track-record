"""Group broker round turns into strategy trades.

The problem, in the operator's words: "Tradovate may sometimes report one
position as several separate fills -- 1 NQ +$680, 1 NQ +$675 may actually be
one 2 NQ strategy trade."

Both exports split positions, and each proves the linkage differently:

    Tradovate  legs that share a fill id are two matches against one order
    TPT        legs that share an entry or exit instant to the millisecond,
               in one account, contract and direction, are one position

Neither is a guess. Sources publish their linkage as `link_keys`; a trade is
a connected component of the graph those keys induce. The two rules were run
against the same period and produce identical trades -- see
tests/test_grouping.py.

Cases no rule can see -- a deliberate scale-in placed as two independent
orders at different times -- are surfaced as review candidates and merged only
by an explicit, published override.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .model import Leg

#: Two same-direction legs in one contract whose lifetimes nearly touch are
#: *flagged*, never auto-merged.
MAX_REVIEW_FLAGS = 8
#: Hard stop on the pair scan for one trade. Beyond this the answer is
#: "a lot", and enumerating the rest only costs time on an unattended job.
SCAN_CAP = 200
REVIEW_WINDOW = timedelta(minutes=5)


class _Union:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


@dataclass
class Trade:
    """One strategy trade: one or more broker round turns on one position."""

    trade_id: str
    root: str
    symbol: str
    legs: list[Leg] = field(default_factory=list)
    override: str | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def source(self) -> str:
        return self.legs[0].source

    @property
    def account(self) -> str | None:
        return self.legs[0].account

    @property
    def qty(self) -> float:
        return sum(leg.qty for leg in self.legs)

    @property
    def gross_pnl(self) -> float:
        return round(sum(leg.gross_pnl for leg in self.legs), 6)

    @property
    def reported_commission(self) -> float | None:
        """Actual commission, or None if any leg's source did not report it."""
        if any(leg.commission is None for leg in self.legs):
            return None
        return round(sum(leg.commission for leg in self.legs), 6)   # type: ignore[misc]

    @property
    def direction(self) -> str:
        return self.legs[0].direction

    @property
    def is_short(self) -> bool:
        return self.direction == "short"

    @property
    def opened_at(self) -> datetime:
        return min(leg.opened_at for leg in self.legs)

    @property
    def closed_at(self) -> datetime:
        """When the position went flat. P&L is realised here, so this dates it."""
        return max(leg.closed_at for leg in self.legs)

    @property
    def session_hint(self) -> date | None:
        hints = {leg.session_hint for leg in self.legs if leg.session_hint}
        return max(hints) if hints else None

    @property
    def entry_price(self) -> float:
        """Quantity-weighted, so a scaled entry reports the price the position
        actually carried rather than whichever leg happened to be first."""
        return round(sum(leg.entry_price * leg.qty for leg in self.legs) / self.qty, 6)

    @property
    def exit_price(self) -> float:
        return round(sum(leg.exit_price * leg.qty for leg in self.legs) / self.qty, 6)


def group_legs(legs: list[Leg]) -> list[Trade]:
    """Connected components over each source's declared linkage."""
    uf = _Union()
    buckets: dict[str, list[int]] = {}
    for idx, leg in enumerate(legs):
        uf.find(idx)
        for k in leg.link_keys:
            buckets.setdefault(k, []).append(idx)

    for bucket in buckets.values():
        for other in bucket[1:]:
            uf.union(bucket[0], other)

    components: dict[int, list[int]] = {}
    for idx in range(len(legs)):
        components.setdefault(uf.find(idx), []).append(idx)

    trades: list[Trade] = []
    for root_idx in sorted(components):
        members = [legs[i] for i in sorted(components[root_idx])]
        if len({m.root for m in members}) > 1:
            raise ValueError(f"a link key spans contracts: {[m.leg_id for m in members]}")
        t = Trade(trade_id="", root=members[0].root,
                  symbol=members[0].symbol, legs=members)
        if len({m.direction for m in members}) > 1:
            t.flags.append("mixed_direction_legs")
        trades.append(t)

    trades.sort(key=lambda t: (t.closed_at, t.legs[0].leg_id))
    for t in trades:
        # Identity from the BROKER's ids, never a running counter. A sequence
        # number is a function of everything else in the file, so re-importing
        # a longer export -- the normal case, since both vendors export the
        # period to date -- would renumber trades already published and
        # hash-chained. Keying on the smallest leg id makes the id a function
        # of the trade alone: stable, order-independent, reproducible.
        anchor = min(leg.leg_id for leg in t.legs)
        t.trade_id = f"{t.closed_at:%Y%m%d}-{anchor}"

    _flag_review_candidates(trades)
    return trades


def _flag_review_candidates(trades: list[Trade]) -> None:
    """Mark trades no linkage could join but a human might want to.

    Advisory only. Nothing here changes a number; it changes what the operator
    is asked to look at.
    """
    # The rule is unchanged: flag b against a when b opens no later than
    # REVIEW_WINDOW after a closes -- i.e. the two positions overlap or abut.
    # Two things about the old implementation were wrong.
    #
    # The `break` tested `b.opened_at`, but the list is sorted by `closed_at`,
    # so `opened_at` is not monotonic and the break fired almost never. The
    # scan is now bounded by a bisect over an open-time index, which IS
    # monotonic, so the early exit is sound rather than decorative.
    #
    # And the output was uncapped. Positions opened together and closed apart
    # legitimately flag every pair, so 800 such trades produced 640k notes and
    # wrote 23 MB of flag text into trades.csv -- on a job that runs
    # unattended. The cap keeps that bounded; a truncated trade says so in its
    # own flags rather than quietly showing a short list.
    order = sorted(range(len(trades)), key=lambda k: trades[k].opened_at)
    opens = [trades[k].opened_at for k in order]
    seen: list[set] = [set() for _ in trades]

    for i, a in enumerate(trades):
        hi = bisect.bisect_right(opens, a.closed_at + REVIEW_WINDOW)
        for pos in range(hi):
            if len(seen[i]) >= SCAN_CAP:
                break
            j = order[pos]
            if j <= i:
                continue
            b = trades[j]
            if a.root != b.root or a.direction != b.direction:
                continue
            for x, y, k in ((a, b, i), (b, a, j)):
                note = f"possible_scale_with:{y.trade_id}"
                if note in seen[k]:
                    continue
                seen[k].add(note)
                if len(seen[k]) <= MAX_REVIEW_FLAGS:
                    x.flags.append(note)

    for k, t in enumerate(trades):
        n = len(seen[k])
        if n > MAX_REVIEW_FLAGS:
            t.flags.append(
                f"scale_candidates_truncated:{n}{'+' if n >= SCAN_CAP else ''}")


def apply_overrides(trades: list[Trade], overrides: dict) -> list[Trade]:
    """Apply the operator's published corrections.

    `overrides` is a committed, hash-chained file -- an unpublished correction
    is indistinguishable from editing the record, so every one is visible.

        merge:    [[trade_id, trade_id, ...], ...]
        split:    [trade_id, ...]        -> one trade per leg
        exclude:  [trade_id, ...]
        reviewed: {trade_id: reason, ...}

    `reviewed` is the fourth case and the one the first three could not
    express: the operator looked at a flagged trade and decided it was already
    right. Without it the only way to clear a review flag was to change the
    record, so "I checked and it stands" and "I have not looked yet" were
    indistinguishable to a reader -- and the flags are public. Recording the
    decision replaces the machine's `possible_scale_with` guess with the
    operator's stated reason, which is a stronger disclosure than silence and a
    much stronger one than a merge nobody can audit.
    """
    by_id = {t.trade_id: t for t in trades}
    dropped: set[str] = set()
    result: list[Trade] = []

    for group in overrides.get("merge", []):
        members = [by_id[t] for t in group if t in by_id]
        if len(members) < 2:
            continue
        result.append(Trade(
            trade_id=min(m.trade_id for m in members),
            root=members[0].root, symbol=members[0].symbol,
            legs=[leg for m in members for leg in m.legs],
            override=f"merge:{'+'.join(sorted(m.trade_id for m in members))}",
        ))
        dropped.update(m.trade_id for m in members)

    for tid in overrides.get("split", []):
        t = by_id.get(tid)
        if t is None:
            continue
        for leg in t.legs:
            result.append(Trade(
                trade_id=f"{t.closed_at:%Y%m%d}-{leg.leg_id}",
                root=t.root, symbol=t.symbol, legs=[leg],
                override=f"split:{t.trade_id}",
            ))
        dropped.add(tid)

    dropped.update(overrides.get("exclude", []))
    result.extend(t for t in trades if t.trade_id not in dropped)

    reviewed = overrides.get("reviewed", {}) or {}
    if not isinstance(reviewed, dict):
        raise ValueError("overrides.json: `reviewed` must be a map of "
                         "trade_id -> reason")
    for tr in result:
        reason = reviewed.get(tr.trade_id)
        if reason is None:
            continue
        tr.flags[:] = [f for f in tr.flags if not f.startswith("possible_scale_with:")]
        tr.flags.append(f"reviewed:{reason}")

    unknown = sorted(set(reviewed) - {tr.trade_id for tr in result})
    if unknown:
        raise ValueError("overrides.json: `reviewed` names trades that do not "
                         "exist: " + ", ".join(unknown))

    result.sort(key=lambda t: (t.closed_at, t.trade_id))
    return result
