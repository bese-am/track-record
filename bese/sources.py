"""Readers for the two export formats, and a sniffer that picks between them.

Drop either kind of file in the inbox. The publisher works out which it is
from the header, so the operator never has to name a format or keep two
folders.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo
from pathlib import Path

from .contracts import CONTRACTS, parse_root
from .model import Leg

# --------------------------------------------------------------------------
# Take Profit Trader -- "completed trades" export. PREFERRED SOURCE.
# --------------------------------------------------------------------------

TPT_HEADER = {"tradeId", "tradeAccount", "pnlDollars", "commission", "entryDate"}

#: Legs on one position share an entry or an exit instant to the millisecond.
#: TPT reports no fill ids, so this is the linkage available -- but an exact
#: match on a millisecond timestamp within one account, symbol and direction
#: is not a heuristic in any meaningful sense: two distinct positions opened
#: at the identical millisecond are one position. Verified against the
#: Tradovate fill-id linkage on the same period; see tests/test_grouping.py.
def _tpt_link_keys(account: str, root: str, direction: str,
                   opened: datetime, closed: datetime) -> tuple[str, ...]:
    stem = f"{account}|{root}|{direction}"
    return (f"{stem}|in|{opened.isoformat()}", f"{stem}|out|{closed.isoformat()}")


#: The furthest ahead a session may plausibly be dated. A timestamp beyond this
#: is a corrupt row, not a trade, and letting one through dates a session in the
#: year 9999 and permanently poisons the chain's ordering.
MAX_FUTURE_DAYS = 7


def num(raw, field: str, *, allow_negative: bool = True) -> float:
    """float(), but it rejects the values that defeat every downstream guard.

    Python's `float()` happily returns NaN and Infinity, and every validation
    in this codebase is a `> threshold` comparison. Comparisons against NaN are
    all False, so a single NaN price walked past the P&L reconciliation, past
    the compounding-identity check, and into a hash-chained snapshot -- one
    that Python could read back and a conformant JSON parser could not.
    """
    v = float(raw)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"{field}: {raw!r} is not a finite number")
    if not allow_negative and v < 0:
        raise ValueError(f"{field}: {raw!r} is negative")
    return v


def check_time(ts, field: str):
    from datetime import datetime, timedelta, timezone
    limit = datetime.now(timezone.utc) + timedelta(days=MAX_FUTURE_DAYS)
    if ts > limit:
        raise ValueError(f"{field}: {ts.isoformat()} is in the future")
    return ts


def read_tpt(path: Path) -> list[Leg]:
    out: list[Leg] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if not (r.get("tradeId") or "").strip():
                continue
            root = parse_root(r["symbol"])
            direction = "long" if r["position"].strip().upper() == "L" else "short"
            qty = num(r["maxQuantity"], "maxQuantity", allow_negative=False)
            entry = num(r["entryPrice"], "entryPrice")
            exit_ = num(r["exitPrice"], "exitPrice")
            # A negative commission is a rebate, or a corrupt cell. Either way
            # it inflates net P&L and gets published as cost_basis "reported"
            # -- flagged in the record as the authoritative charged figure.
            commission = num(r["commission"], "commission", allow_negative=False)
            net = num(r["pnlDollars"], "pnlDollars")

            sign = 1 if direction == "long" else -1
            gross = round((exit_ - entry) * CONTRACTS[root].point_value * qty * sign, 6)
            notes: tuple[str, ...] = ()
            # TPT states net P&L and commission separately; the two must agree
            # with the price move and the multiplier or a contract spec is wrong.
            if abs(gross - commission - net) > 0.005:
                notes += (f"pnl_disagrees:gross={gross} comm={commission} net={net}",)

            opened = check_time(_iso(r["entryDate"]), "entryDate")
            closed = check_time(_iso(r["exitDate"]), "exitDate")
            out.append(Leg(
                source="tpt",
                leg_id=r["tradeId"].strip(),
                account=r["tradeAccount"].strip() or None,
                symbol=r["symbol"].strip(),
                root=root,
                direction=direction,
                qty=qty,
                entry_price=entry,
                exit_price=exit_,
                opened_at=opened,
                closed_at=closed,
                gross_pnl=gross,
                commission=commission,
                session_hint=_iso(r["exitDay"]).date() if r.get("exitDay") else None,
                link_keys=_tpt_link_keys(
                    r["tradeAccount"].strip(), root, direction, opened, closed),
                notes=notes,
            ))
    return out


def _iso(s: str) -> datetime:
    """Parse an ISO-8601 instant. The trailing Z is an explicit UTC offset --
    which is the single biggest reason to prefer this export: no timezone has
    to be assumed, so no session date can be silently wrong."""
    return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))


# --------------------------------------------------------------------------
# Tradovate -- "Performance" export. FALLBACK / CORROBORATION.
# --------------------------------------------------------------------------

TRADOVATE_HEADER = {"buyFillId", "sellFillId", "boughtTimestamp", "soldTimestamp"}

#: This export prints NO offset, so one has to be declared. It is the trader's
#: Tradovate display timezone, not UTC -- established empirically by matching
#: the same trades against the TPT export, whose timestamps carry an explicit
#: Z: every Tradovate stamp sits exactly one hour ahead of the TPT instant,
#: i.e. Europe/London on BST. Guessing UTC here was wrong and would have
#: mis-dated any position closed between 16:00 and 17:00 New York time.
TRADOVATE_TZ = ZoneInfo("Europe/London")
TRADOVATE_TS = "%m/%d/%Y %H:%M:%S"
_MONEY = re.compile(r"^\$?\(?\s*(-?[\d,]+(?:\.\d+)?)\s*\)?$")


def parse_money(raw: str) -> float:
    """'$1,720.00' -> 1720.0 ; '$(292.50)' -> -292.5

    Accounting parentheses are the negative sign in this export. Reading them
    as positive would turn every loss into a gain, so an unparseable value
    raises rather than defaulting to zero.
    """
    text = raw.strip()
    m = _MONEY.match(text)
    if not m:
        raise ValueError(f"unparseable money value {raw!r}")
    return -float(m.group(1).replace(",", "")) if "(" in text else float(
        m.group(1).replace(",", ""))


def read_tradovate(path: Path, source_tz: tzinfo = TRADOVATE_TZ) -> list[Leg]:
    """`source_tz` must be declared: this export prints no offset."""
    out: list[Leg] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if not (r.get("symbol") or "").strip():
                continue
            bought = check_time(datetime.strptime(
                r["boughtTimestamp"].strip(), TRADOVATE_TS)
                .replace(tzinfo=source_tz), "boughtTimestamp")
            sold = check_time(datetime.strptime(
                r["soldTimestamp"].strip(), TRADOVATE_TS)
                .replace(tzinfo=source_tz), "soldTimestamp")
            short = sold < bought
            stated = parse_money(r["pnl"])
            _qty = num(r["qty"], "qty", allow_negative=False)
            _entry = num(r["sellPrice"] if short else r["buyPrice"], "price")
            _exit = num(r["buyPrice"] if short else r["sellPrice"], "price")
            _sign = -1 if short else 1
            _root = parse_root(r["symbol"])
            _gross = round((_exit - _entry) * CONTRACTS[_root].point_value
                           * _qty * _sign, 6)
            # This reader used to take the broker's stated P&L on trust while
            # the publisher reported that every leg had been reconciled. A row
            # declaring $500,000 on a 1-lot passed straight through.
            tv_notes: tuple[str, ...] = ()
            if abs(_gross - stated) > 0.005:
                tv_notes += (f"pnl_disagrees:computed={_gross} stated={stated}",)

            out.append(Leg(
                source="tradovate",
                leg_id=f"{r['buyFillId'].strip()}/{r['sellFillId'].strip()}",
                account=None,
                symbol=r["symbol"].strip(),
                root=parse_root(r["symbol"]),
                direction="short" if short else "long",
                qty=num(r["qty"], "qty", allow_negative=False),
                entry_price=num(r["sellPrice"] if short else r["buyPrice"], "price"),
                exit_price=num(r["buyPrice"] if short else r["sellPrice"], "price"),
                opened_at=sold if short else bought,
                closed_at=bought if short else sold,
                gross_pnl=stated,
                commission=None,          # not reported -> rate card stands in
                session_hint=None,
                # Exact broker linkage: Tradovate splits one position into
                # several rows that share a fill id on the un-split side.
                link_keys=(f"fill|{r['buyFillId'].strip()}",
                           f"fill|{r['sellFillId'].strip()}"),
                notes=tv_notes,
            ))
    return out


# --------------------------------------------------------------------------

def sniff(path: Path) -> str:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        header = set(next(csv.reader(fh), []))
    if header >= TPT_HEADER:
        return "tpt"
    if header >= TRADOVATE_HEADER:
        return "tradovate"
    raise ValueError(f"{path.name}: not a recognised Besë export")


def read_any(path: Path, source_tz: tzinfo = TRADOVATE_TZ) -> list[Leg]:
    kind = sniff(path)
    return read_tpt(path) if kind == "tpt" else read_tradovate(path, source_tz)


def read_many(paths: list[Path], source_tz: tzinfo = TRADOVATE_TZ,
              prefer: str = "tpt") -> tuple[list[Leg], dict]:
    """Read every export and reduce to one leg per underlying round turn.

    Two kinds of duplication are handled, and they are different problems:

    1. **The same export, re-read.** Tradovate and TPT both export the period
       to date, so consecutive pulls overlap by design. Same source, same
       `leg_id` -> one leg.

    2. **The same trade seen in BOTH formats.** These have no id in common, so
       they are matched on the economics: account, contract, direction, size,
       both instants and both prices. When a trade appears in both, the
       preferred source wins -- TPT, because it reports the commission that
       was actually charged instead of one this code models.
    """
    stats = {"files": 0, "rows": 0, "by_source": {}, "cross_format": 0,
             "conflicts": [], "notes": []}
    by_leg: dict[tuple[str, str], Leg] = {}

    for p in sorted(paths):
        kind = sniff(p)
        legs = read_any(p, source_tz)
        stats["files"] += 1
        stats["rows"] += len(legs)
        stats["by_source"][kind] = stats["by_source"].get(kind, 0) + len(legs)
        for leg in legs:
            key = (leg.source, leg.leg_id)
            prior = by_leg.get(key)
            if prior is not None:
                if (prior.qty, prior.gross_pnl) != (leg.qty, leg.gross_pnl):
                    stats["conflicts"].append(
                        f"{leg.source} leg {leg.leg_id} differs between exports")
                continue
            by_leg[key] = leg
            stats["notes"].extend(f"{leg.source}:{leg.leg_id} {n}" for n in leg.notes)

    # Collapse across formats.
    chosen: dict[tuple, Leg] = {}
    for leg in by_leg.values():
        k = leg.dedupe_key
        prior = chosen.get(k)
        if prior is None:
            chosen[k] = leg
            continue
        stats["cross_format"] += 1
        if abs(prior.gross_pnl - leg.gross_pnl) > 0.005:
            stats["conflicts"].append(
                f"same trade, different gross P&L across formats: "
                f"{prior.source} {prior.gross_pnl} vs {leg.source} {leg.gross_pnl}")
        if leg.source == prefer and prior.source != prefer:
            chosen[k] = leg

    out = sorted(chosen.values(), key=lambda leg: (leg.closed_at, leg.leg_id))
    return out, stats
