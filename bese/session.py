"""CME session dating.

A futures trading day is not a calendar day. The CME equity-index session runs
17:00 ET to 16:00 ET the following day, so a position opened Sunday evening
belongs to Monday's session. Dating trades by naive calendar date -- London
calendar date especially -- would cut the US session in half and put one
session's P&L into two rows of nav.csv.

A trade is dated by when its P&L was REALISED, i.e. when the position went
flat, not when it was opened.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EXCHANGE_TZ = ZoneInfo("America/Chicago")   # CME clock
CME_ROLLOVER = time(17, 0)                  # 17:00 ET / 16:00 CT boundary
_ET = ZoneInfo("America/New_York")


def session_date(ts: datetime) -> date:
    """The CME session date a timestamp falls in.

    At or after 17:00 ET, the session belongs to the NEXT calendar day.
    Weekend timestamps roll forward to Monday.
    """
    local = ts.astimezone(_ET)
    d = local.date()
    if local.time() >= CME_ROLLOVER:
        d += timedelta(days=1)
    while d.weekday() >= 5:                 # Sat/Sun -> Monday
        d += timedelta(days=1)
    return d


def closes_after_rollover(ts: datetime) -> bool:
    """True when a position went flat at or after the 17:00 ET boundary.

    Take Profit Trader requires every position closed by 5:00 PM ET and permits
    no overnight holding -- which is exactly the CME rollover. So under this
    firm's rules, no trade can straddle a session boundary, and the session
    date is unambiguous.

    That turns this predicate into a free timezone validator. The Tradovate
    export prints no offset, and a wrong `source_tz` shifts every timestamp; if
    the shift is large enough to push a close past 17:00 ET, this fires. A hit
    means one of exactly two things, and both need a human:

      - `SOURCE_TZ` in the publisher is wrong, so every session date is suspect
      - a TPT rule was actually breached, which the operator wants to know

    The residual case this cannot see is a one-hour error (UTC vs London), and
    it only bites for a position closed between 16:00 and 17:00 ET. Confirm the
    timezone in the Tradovate profile once and it is settled for good.
    """
    return ts.astimezone(_ET).time() >= CME_ROLLOVER


def previous_session_date(d: date) -> date:
    """The trading day before `d` -- the funded-capital anchor for inception."""
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
