"""Normalise strategy trades to 1 NQ-equivalent exposure.

    nq_equiv         = qty * point_value / 20
    standardised P&L = (gross P&L - costs) / nq_equiv

Costs come off BEFORE the division, so they scale with the position the way
the P&L does. That has a consequence worth stating rather than hiding: at the
firm's rates a micro round turn costs $1.50 against $4.50 for an E-mini, but
carries a tenth of the exposure -- so per unit of NQ-equivalent risk the micro
is $15.00 against $4.50, over three times the cost. The normalisation must
carry that through, or the record would credit the strategy with an efficiency
it did not have.

The same amplification applies to any ERROR in the cost figure, which is why
`cost_basis` is tracked per trade. A $1 mistake on a 1-lot MNQ moves the
published record by $10.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .contracts import nq_equivalent, round_turn_cost
from .model import account_ref
from .group import Trade
from .session import session_date


@dataclass(frozen=True)
class NormalisedTrade:
    trade_id: str
    session: date
    symbol: str
    root: str
    direction: str
    qty: float
    nq_equiv: float
    entry_price: float
    exit_price: float
    opened_at: str
    closed_at: str
    gross_pnl: float
    costs: float
    #: "reported" -- the commission the firm actually charged.
    #: "modelled"  -- the rate card in contracts.py stood in.
    cost_basis: str
    net_pnl: float
    standardised_pnl: float
    legs: int
    source: str
    account_ref: str | None
    override: str | None
    flags: str


def normalise(trade: Trade) -> NormalisedTrade:
    equiv = nq_equivalent(trade.root, trade.qty)
    if equiv <= 0:
        raise ValueError(f"{trade.trade_id}: non-positive NQ-equivalent")

    reported = trade.reported_commission
    if reported is None:
        costs, basis = round_turn_cost(trade.root, trade.qty), "modelled"
    else:
        costs, basis = reported, "reported"

    net = round(trade.gross_pnl - costs, 6)

    # The firm's own session label wins when it publishes one: it is the
    # authority on which day it thinks the trade belongs to, and it removes
    # any dependence on inferring a timezone. Fall back to the CME rule.
    session = trade.session_hint or session_date(trade.closed_at)

    return NormalisedTrade(
        trade_id=trade.trade_id,
        session=session,
        symbol=trade.symbol,
        root=trade.root,
        direction=trade.direction,
        qty=trade.qty,
        nq_equiv=round(equiv, 6),
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        opened_at=trade.opened_at.isoformat(),
        closed_at=trade.closed_at.isoformat(),
        gross_pnl=round(trade.gross_pnl, 2),
        costs=round(costs, 2),
        cost_basis=basis,
        net_pnl=round(net, 2),
        standardised_pnl=round(net / equiv, 2),
        legs=len(trade.legs),
        source=trade.source,
        account_ref=account_ref(trade.account),
        override=trade.override,
        flags=";".join(trade.flags),
    )


def normalise_all(trades: list[Trade]) -> list[NormalisedTrade]:
    return [normalise(t) for t in trades]
