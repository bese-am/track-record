"""Contract specifications and the NQ-equivalence rule.

The whole Bese normalisation rests on one number per instrument: the dollar
value of one index point. Everything else -- equivalence, standardised P&L,
cost scaling -- is derived from it, so this table is the single place a
contract fact is allowed to live.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The exposure unit the track record is denominated in.
BASE_SYMBOL = "NQ"


@dataclass(frozen=True)
class Contract:
    root: str
    name: str
    point_value: float          # USD per index point, per contract
    tick_size: float
    #: All-in round-turn cost per contract in USD, as charged by the prop firm.
    #: Confirmed by the operator 15 Aug 2026. Published in meta.json and echoed
    #: into every snapshot, so any standardised figure can be reproduced.
    #:
    #: Note the asymmetry this creates once costs are expressed per unit of
    #: NQ-equivalent exposure -- see COST_PER_NQ_EQUIVALENT below. It is a real
    #: property of trading micros, not an artefact of the normalisation, and it
    #: is why the record must not be published gross.
    round_turn_cost: float


CONTRACTS: dict[str, Contract] = {
    "NQ": Contract("NQ", "E-mini Nasdaq-100", 20.0, 0.25, 4.50),
    "MNQ": Contract("MNQ", "Micro E-mini Nasdaq-100", 2.0, 0.25, 1.50),
}

BASE_POINT_VALUE = CONTRACTS[BASE_SYMBOL].point_value


def parse_root(symbol: str) -> str:
    """'MNQU6' -> 'MNQ'. Contract month and year are irrelevant to the record:
    a roll is not an event in a strategy's history, only in a broker's."""
    s = symbol.strip().upper()
    # Longest root first, so MNQ is never mis-read as NQ.
    for root in sorted(CONTRACTS, key=len, reverse=True):
        if s.startswith(root):
            return root
    raise KeyError(f"unknown contract root in symbol {symbol!r}")


def nq_equivalent(root: str, qty: float) -> float:
    """Contracts -> NQ-equivalent exposure.

    1 NQ -> 1.0 | 2 NQ -> 2.0 | 5 MNQ -> 0.5 | 10 MNQ -> 1.0
    """
    return qty * CONTRACTS[root].point_value / BASE_POINT_VALUE


def round_turn_cost(root: str, qty: float) -> float:
    """Actual dollar cost of opening and closing `qty` contracts."""
    return qty * CONTRACTS[root].round_turn_cost


def cost_per_nq_equivalent(root: str) -> float:
    """What one NQ-equivalent of exposure costs to round-trip in `root`.

    NQ  : $4.50 per contract, 1.0 equivalent  -> $4.50
    MNQ : $1.50 per contract, 0.1 equivalent  -> $15.00

    A micro costs 3.33x as much per unit of risk carried. The normalisation
    must transmit that -- dividing a gross P&L by the equivalent while leaving
    the cost outside would credit the strategy with an efficiency it did not
    have, and would make the record flattering in exactly the direction a
    reader is right to be suspicious of.
    """
    c = CONTRACTS[root]
    return c.round_turn_cost / (c.point_value / BASE_POINT_VALUE)


#: Published in meta.json so the asymmetry is stated, not left to be noticed.
COST_PER_NQ_EQUIVALENT = {r: round(cost_per_nq_equivalent(r), 2) for r in CONTRACTS}
