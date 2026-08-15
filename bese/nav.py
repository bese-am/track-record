"""Build the $100,000 nominal NAV series.

    NAV_0 = 100,000                      at the funded-capital anchor
    NAV_t = NAV_{t-1} + standardised P&L_t

The anchor is the session BEFORE the first trade, following RVB's rule: the
first session already contains its own P&L, so starting the curve there would
silently delete day one.

Daily return is NAV_t / NAV_{t-1} - 1, identical to RVB. The identity

    prod(1 + daily_return) == NAV_last / NAV_0

holds exactly, which is why every metric, chart and table downstream of this
file works on the Bese series with no modification.

Note the convention, and state it in the methodology: exposure is held at 1
NQ-equivalent regardless of NAV, so position size does not compound with
equity. The RETURN series compounds correctly; the STRATEGY is a
constant-notional one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .normalize import NormalisedTrade
from .session import previous_session_date

NOMINAL_CAPITAL = 100_000.0


@dataclass(frozen=True)
class NavPoint:
    date: date
    equity: float
    pnl: float | None
    daily_return: float | None
    trades: int


def build_nav(
    trades: list[NormalisedTrade],
    nominal_capital: float = NOMINAL_CAPITAL,
) -> list[NavPoint]:
    if not trades:
        return []

    by_session: dict[date, list[NormalisedTrade]] = {}
    for t in trades:
        by_session.setdefault(t.session, []).append(t)

    sessions = sorted(by_session)
    anchor = previous_session_date(sessions[0])

    out = [NavPoint(anchor, round(nominal_capital, 2), None, None, 0)]
    equity = nominal_capital

    for s in sessions:
        pnl = round(sum(t.standardised_pnl for t in by_session[s]), 2)
        prev = equity
        equity = round(prev + pnl, 2)
        out.append(
            NavPoint(
                date=s,
                equity=equity,
                pnl=pnl,
                daily_return=equity / prev - 1,
                trades=len(by_session[s]),
            )
        )
    return out


def cumulative_return(nav: list[NavPoint]) -> float | None:
    if len(nav) < 2:
        return None
    return nav[-1].equity / nav[0].equity - 1
