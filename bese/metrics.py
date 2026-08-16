"""The metric engine. Every published statistic is computed here.

This module is public because the NAV series it consumes is CONSTRUCTED from
broker fills rather than read from an account balance. A track record whose
inputs are constructed and whose arithmetic is hidden offers a reader nothing
to check, so every published payload names this file and it is open.

Deliberately pure Python: no numpy, no pandas. A reader reproducing these
numbers should not have to match a library version to get the same answer.

Conventions, stated rather than assumed:

  * annualisation basis 252
  * Sharpe / Sortino / Calmar are EXCESS of the risk-free rate; gross variants
    published alongside
  * daily risk-free is geometric: (1 + rf_annual) ** (1/252) - 1
  * annualised statistics are withheld below 60 sessions
  * a withheld or undefined value is None, never 0
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date

ANNUAL_BASIS = 252
MIN_SESSIONS_FOR_ANNUALISED = 60

SUPPRESSED_BELOW_GATE = [
    "sharpe", "sharpe_gross", "sharpe_autocorr_adj", "sortino", "sortino_gross",
    "calmar", "calmar_gross", "cagr", "volatility", "max_drawdown",
    "var_normal_95", "skew", "kurtosis", "ev_excess_annual", "win_rate",
]

ROLLING_WINDOWS = (30, 60, 90)


# ------------------------------------------------------------------ basics ---

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: list[float]) -> float | None:
    """Sample standard deviation. Undefined on fewer than two observations —
    and undefined means None, not zero."""
    n = len(xs)
    if n < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _quantile(sorted_xs: list[float], q: float) -> float:
    """Linear interpolation between order statistics (the R-7 / numpy default)."""
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    pos = (len(sorted_xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_xs[int(pos)]
    return sorted_xs[lo] * (hi - pos) + sorted_xs[hi] * (pos - lo)


def daily_risk_free(rf_annual: float) -> float:
    return (1 + rf_annual) ** (1 / ANNUAL_BASIS) - 1


# ---------------------------------------------------------------- the maths ---

def compute_equity_curve(nav: list[tuple[date, float]]) -> list[float]:
    base = nav[0][1]
    return [e / base - 1 for _, e in nav]


def compute_max_drawdown(equity: list[float]) -> tuple[float | None, list[float]]:
    """Return (max drawdown, the drawdown path). Both from the running peak."""
    peak = -math.inf
    path: list[float] = []
    for e in equity:
        peak = max(peak, e)
        path.append(e / peak - 1 if peak > 0 else 0.0)
    return (min(path) if path else None), path


def compute_sharpe(returns: list[float], rf_annual: float,
                   excess: bool = True) -> float | None:
    if len(returns) < 2:
        return None
    rf_d = daily_risk_free(rf_annual) if excess else 0.0
    xs = [r - rf_d for r in returns]
    sd = _stdev(xs)
    if not sd:
        return None
    return _mean(xs) / sd * math.sqrt(ANNUAL_BASIS)


def compute_sortino(returns: list[float], rf_annual: float,
                    excess: bool = True) -> float | None:
    if len(returns) < 2:
        return None
    rf_d = daily_risk_free(rf_annual) if excess else 0.0
    xs = [r - rf_d for r in returns]
    downside = math.sqrt(sum(min(x, 0.0) ** 2 for x in xs) / len(xs))
    if downside == 0:
        return None            # no downside observed: undefined, not infinite
    return _mean(xs) / downside * math.sqrt(ANNUAL_BASIS)


def compute_sharpe_autocorr_adj(returns: list[float], rf_annual: float) -> float | None:
    """Sharpe corrected for serial correlation — Lo (2002).

    The naive sqrt(q) annualisation assumes independent returns. When returns
    are autocorrelated it overstates the ratio, sometimes badly. Lo's scaling
    factor replaces sqrt(q) with q / sqrt(q + 2 * sum_{k<q} (q-k) * rho_k).
    """
    sr_daily_ann = compute_sharpe(returns, rf_annual)
    if sr_daily_ann is None or len(returns) < 3:
        return None
    sr_daily = sr_daily_ann / math.sqrt(ANNUAL_BASIS)

    n, q = len(returns), ANNUAL_BASIS
    m = _mean(returns)
    denom = sum((r - m) ** 2 for r in returns)
    if denom == 0:
        return None

    total = float(q)
    for k in range(1, min(q, n - 1)):
        cov = sum((returns[i] - m) * (returns[i + k] - m) for i in range(n - k))
        total += 2 * (q - k) * (cov / denom)
    if total <= 0:
        return None
    return sr_daily * q / math.sqrt(total)


def compute_skew(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    m, sd = _mean(xs), _stdev(xs)
    if not sd:
        return None
    g1 = sum((x - m) ** 3 for x in xs) / n / sd ** 3
    return g1 * math.sqrt(n * (n - 1)) / (n - 2)          # sample-adjusted


def compute_kurtosis(xs: list[float]) -> float | None:
    """Excess kurtosis, sample-adjusted (Fisher)."""
    n = len(xs)
    if n < 4:
        return None
    m, sd = _mean(xs), _stdev(xs)
    if not sd:
        return None
    g2 = sum((x - m) ** 4 for x in xs) / n / sd ** 4 - 3
    return ((n - 1) * ((n + 1) * g2 + 6)) / ((n - 2) * (n - 3))


# ------------------------------------------------------------------ payloads ---

@dataclass
class MetricInputs:
    nav: list[tuple[date, float]]        # includes the anchor point
    returns: list[tuple[date, float]]    # one shorter than nav
    rf_annual: float
    rf_source: str


def compute_core_metrics(inp: MetricInputs,
                         min_sessions: int = MIN_SESSIONS_FOR_ANNUALISED) -> dict:
    """Every published metric. The only place any of them is calculated."""
    rs = [r for _, r in inp.returns]
    n = len(rs)
    mdd, _path = compute_max_drawdown([e for _, e in inp.nav])

    cumulative = inp.nav[-1][1] / inp.nav[0][1] - 1 if len(inp.nav) > 1 else None
    years = n / ANNUAL_BASIS if n else 0
    cagr = ((1 + cumulative) ** (1 / years) - 1) if (cumulative is not None
                                                     and years > 0
                                                     and 1 + cumulative > 0) else None

    sd = _stdev(rs)
    vol = sd * math.sqrt(ANNUAL_BASIS) if sd else None
    sharpe = compute_sharpe(rs, inp.rf_annual)
    ev_excess = (_mean(rs) - daily_risk_free(inp.rf_annual)) * ANNUAL_BASIS if n else None

    calmar = (cagr - inp.rf_annual) / abs(mdd) if (cagr is not None and mdd) else None
    calmar_gross = cagr / abs(mdd) if (cagr is not None and mdd) else None

    values: dict[str, float | int | None] = {
        # Statements of what happened — published from session one, because
        # they are observations rather than estimates of anything.
        "cumulative_return": cumulative,
        "best_day": max(rs) if rs else None,
        "worst_day": min(rs) if rs else None,
        "n_obs": n,
        "positive_days": sum(1 for r in rs if r > 0),
        "negative_days": sum(1 for r in rs if r < 0),
        # Estimates — gated below.
        "cagr": cagr,
        "volatility": vol,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "sharpe_gross": compute_sharpe(rs, inp.rf_annual, excess=False),
        "sharpe_autocorr_adj": compute_sharpe_autocorr_adj(rs, inp.rf_annual),
        "sortino": compute_sortino(rs, inp.rf_annual),
        "sortino_gross": compute_sortino(rs, inp.rf_annual, excess=False),
        "calmar": calmar,
        "calmar_gross": calmar_gross,
        "var_normal_95": (_mean(rs) - 1.6448536269514722 * sd) if sd else None,
        "skew": compute_skew(rs),
        "kurtosis": compute_kurtosis(rs),
        "ev_excess_annual": ev_excess,
        "win_rate": (sum(1 for r in rs if r > 0) / n) if n else None,
    }

    gate = None
    if n < min_sessions:
        # On a handful of sessions these are not imprecise estimates, they are
        # meaningless ones. Suppressing them is the honest presentation.
        for k in SUPPRESSED_BELOW_GATE:
            values[k] = None
        gate = {
            "have": n,
            "need": min_sessions,
            "suppressed": list(SUPPRESSED_BELOW_GATE),
            "label_en": f"insufficient history — {n}/{min_sessions}",
        }

    payload = {
        "values": values,
        "annualised_basis": ANNUAL_BASIS,
        "risk_free_annual": inp.rf_annual,
        "risk_free_source": inp.rf_source,
        "risk_free_convention": "daily = (1 + annual) ** (1/252) - 1",
        "sharpe_convention": "excess of risk_free_annual (see risk_free_source)",
        "computed_by": "bese.metrics.compute_core_metrics",
    }
    if gate:
        payload["insufficient_history"] = gate
    return payload


def _rolling(returns: list[tuple[date, float]], window: int,
             fn) -> list[dict]:
    out = []
    for i in range(len(returns)):
        if i + 1 < window:
            out.append({"date": returns[i][0].isoformat(), "value": None})
            continue
        chunk = [r for _, r in returns[i + 1 - window: i + 1]]
        out.append({"date": returns[i][0].isoformat(), "value": fn(chunk)})
    return out


def compute_analytics(inp: MetricInputs,
                      min_sessions: int = MIN_SESSIONS_FOR_ANNUALISED) -> dict:
    """The tear-sheet series behind the charts."""
    rs = [r for _, r in inp.returns]
    n = len(rs)
    gated = n < min_sessions
    equity = [e for _, e in inp.nav]
    mdd, path = compute_max_drawdown(equity)

    # --- drawdown episodes ------------------------------------------------
    episodes = []
    start = trough = None
    depth = 0.0
    for i, d in enumerate(path):
        if d < 0 and start is None:
            start, trough, depth = i, i, d
        elif d < 0 and start is not None:
            if d < depth:
                depth, trough = d, i
        elif d >= 0 and start is not None:
            episodes.append({
                "start": inp.nav[start][0].isoformat(),
                "trough": inp.nav[trough][0].isoformat(),
                "recovered": inp.nav[i][0].isoformat(),
                "depth": depth, "sessions": i - start + 1, "ongoing": False,
            })
            start = trough = None
            depth = 0.0
    if start is not None:
        episodes.append({
            "start": inp.nav[start][0].isoformat(),
            "trough": inp.nav[trough][0].isoformat(),
            "recovered": None, "depth": depth,
            "sessions": len(path) - start, "ongoing": True,
        })

    # --- monthly returns --------------------------------------------------
    months: dict[tuple[int, int], list[float]] = {}
    for d, r in inp.returns:
        months.setdefault((d.year, d.month), []).append(r)
    last = inp.returns[-1][0] if inp.returns else None
    monthly = []
    for (y, m), vals in sorted(months.items()):
        comp = 1.0
        for v in vals:
            comp *= 1 + v
        monthly.append({
            "year": y, "month": m, "return": comp - 1, "sessions": len(vals),
            # A month still running is labelled as such rather than presented
            # as a finished figure.
            "partial": bool(last and last.year == y and last.month == m),
        })

    # --- distribution -----------------------------------------------------
    bins: list[dict] = []
    if n >= 2 and max(rs) > min(rs):
        k = max(3, min(20, int(math.sqrt(n))))
        lo, hi = min(rs), max(rs)
        width = (hi - lo) / k
        counts = Counter(min(int((r - lo) / width), k - 1) for r in rs)
        bins = [{"from": lo + i * width, "to": lo + (i + 1) * width,
                 "count": counts.get(i, 0)} for i in range(k)]

    srt = sorted(rs)
    quantiles = [{
        "horizon": "Daily", "n": n,
        "min": srt[0] if srt else None,
        "q25": _quantile(srt, 0.25) if srt else None,
        "median": _quantile(srt, 0.50) if srt else None,
        "q75": _quantile(srt, 0.75) if srt else None,
        "max": srt[-1] if srt else None,
    }]

    rolling_sharpe: dict[str, list[dict]] = {}
    rolling_vol: dict[str, list[dict]] = {}
    rolling_sortino: dict[str, list[dict]] = {}
    withheld_windows = []
    for w in ROLLING_WINDOWS:
        if n < w or gated:
            withheld_windows.append(w)
            continue
        rolling_sharpe[str(w)] = _rolling(
            inp.returns, w, lambda c: compute_sharpe(c, inp.rf_annual))
        rolling_sortino[str(w)] = _rolling(
            inp.returns, w, lambda c: compute_sortino(c, inp.rf_annual))
        rolling_vol[str(w)] = _rolling(
            inp.returns, w,
            lambda c: (_stdev(c) * math.sqrt(ANNUAL_BASIS)) if _stdev(c) else None)

    core = compute_core_metrics(inp, min_sessions)

    return {
        "sessions": n,
        "gated": gated,
        "min_sessions_for_annualised": min_sessions,
        "risk_free_annual": inp.rf_annual,
        "risk_free_source": inp.rf_source,
        "computed_by": ("bese.metrics (compute_core_metrics / compute_sharpe / "
                        "compute_equity_curve / compute_max_drawdown)"),
        "daily_returns": [{"date": d.isoformat(), "return": r} for d, r in inp.returns],
        "drawdown": [{"date": d.isoformat(), "drawdown": v}
                     for (d, _), v in zip(inp.nav, path, strict=True)],
        "drawdown_episodes": episodes,
        # Two routes to the same number must agree, and the payload says so
        # rather than leaving a reader to check.
        "drawdown_consistent_with_metrics": (
            core["values"]["max_drawdown"] is None or
            abs(core["values"]["max_drawdown"] - (mdd or 0)) < 1e-12),
        "monthly_returns": monthly,
        "distribution": {"bins": bins},
        "quantiles": quantiles,
        "rolling_sharpe": rolling_sharpe,
        "rolling_sortino": rolling_sortino,
        "rolling_volatility": rolling_vol,
        "rolling_windows_withheld": withheld_windows,
        "summary": {} if gated else {"ev_excess_annual": core["values"]["ev_excess_annual"]},
        "benchmark_cum": [],
        "withheld_note": (
            f"Rolling statistics and the annualised summary need at least "
            f"{min_sessions} sessions; this book has {n}." if gated else None),
    }
