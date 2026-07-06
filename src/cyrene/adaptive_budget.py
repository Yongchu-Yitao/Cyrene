"""Adaptive Budget Controller — pure rule-based, no ML.

Deterministic controller that dynamically sizes 5-hour and weekly spending
windows based on remaining budget, recent usage velocity, and historical
activity density.  All inputs are explicit — never calls datetime.now().
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Sequence

EPSILON = 1e-8
RESERVE_FACTOR = 0.95
DEFAULT_ACTIVITY_DENSITY = 0.15
MIN_RECALCULATION_INTERVAL_SECONDS = 60.0

WEEKLY_ALPHA = 0.10
FIVE_HOUR_ALPHA = 0.25

WEEKLY_CHANGE_MAX_DECREASE = 0.10  # -10 %
WEEKLY_CHANGE_MAX_INCREASE = 0.25  # +25 %
FIVE_HOUR_CHANGE_MAX_DECREASE = 0.20  # -20 %
FIVE_HOUR_CHANGE_MAX_INCREASE = 0.35  # +35 %


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class UsageRecord:
    """A single token-usage row's cost at a point in time."""
    timestamp: datetime
    cost: float


@dataclass
class BudgetState:
    """Persisted output of one calculation cycle.

    All monetary values are in the configured currency (CNY / USD).  Internal
    computation uses full float precision; rounding is only applied at the API
    serialization layer.
    """
    monthly_budget: float = 0.0
    monthly_spent: float = 0.0
    start_day: int = 1
    weekly_budget: float = 0.0
    weekly_spent: float = 0.0
    five_hour_budget: float = 0.0
    five_hour_spent: float = 0.0
    base_rate: float = 0.0
    recent_rate: float = 0.0
    pressure: float = 0.0
    activity_density: float = 0.0
    weekly_target_raw: float = 0.0
    five_hour_target_raw: float = 0.0
    last_recalculated_at: str = ""
    currency: str = ""
    five_hour_next_refresh_at: str = ""
    weekly_next_refresh_at: str = ""
    monthly_next_refresh_at: str = ""
    five_hour_window_start: str = ""
    weekly_window_start: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _remaining_hours_in_month(now: datetime, start_day: int = 1) -> float:
    """Wall-clock hours remaining in the current billing period from *now*.

    When ``start_day`` is not 1 the period runs from ``start_day`` of this
    month to ``start_day`` of the next month (clamped to month length).
    """
    if start_day == 1:
        y, m = now.year, now.month
        _, days = calendar.monthrange(y, m)
        end = datetime(y, m, days, tzinfo=timezone.utc) + timedelta(days=1)
        delta = end - now
        return max(delta.total_seconds() / 3600.0, 0.0)
    # Custom start day: period is (start_day this month) → (start_day next month)
    y, m = now.year, now.month
    _, days_in_this_month = calendar.monthrange(y, m)
    period_start_day = min(start_day, days_in_this_month)
    period_end_day = min(start_day, calendar.monthrange(y if m < 12 else y + 1, m + 1 if m < 12 else 1)[1]) if m < 12 else min(start_day, 31)
    period_start = datetime(y, m, period_start_day, tzinfo=timezone.utc)
    # If now is before period start, the period started last month
    if now < period_start:
        pm = m - 1 if m > 1 else 12
        py = y if m > 1 else y - 1
        _, days_in_prev = calendar.monthrange(py, pm)
        period_start = datetime(py, pm, min(start_day, days_in_prev), tzinfo=timezone.utc)
    # Period end = start_day of next month
    nm = m + 1 if m < 12 else 1
    ny = y if m < 12 else y + 1
    _, days_in_next = calendar.monthrange(ny, nm)
    period_end = datetime(ny, nm, min(start_day, days_in_next), tzinfo=timezone.utc)
    delta = period_end - now
    return max(delta.total_seconds() / 3600.0, 0.0)


def apply_change_limit(old: float, new: float, max_dec: float, max_inc: float) -> float:
    """Clamp *new* so it doesn't change more than *max_dec* below or *max_inc*
    above the *old* value.  Both *max_dec* and *max_inc* are fractions ∈ [0,1].

    When *old* is exactly 0 (exhausted / fresh start), *new* passes through
    unclamped so the budget is not trapped at zero forever.
    """
    if old == 0:
        return new
    lo = old * (1.0 - max_dec)
    hi = old * (1.0 + max_inc)
    return clamp(new, lo, hi)


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------

def _usage_in_window(records: Sequence[UsageRecord], since: datetime) -> float:
    return sum(
        r.cost for r in records if r.timestamp >= since
    )


def _calculate_rates(
    records: Sequence[UsageRecord],
    now: datetime,
) -> tuple[float, float, float]:
    """Return ``(rate_6h, rate_6_24h, rate_24h_7d)`` in currency/hour.

    Non-overlapping time buckets — each cost contributes to exactly one rate.
    """
    h6 = now - timedelta(hours=6)
    h24 = now - timedelta(hours=24)
    d7 = now - timedelta(days=7)

    spend_0_6 = 0.0
    spend_6_24 = 0.0
    spend_24_168 = 0.0
    for r in records:
        if r.timestamp >= h6:
            spend_0_6 += r.cost
        elif r.timestamp >= h24:
            spend_6_24 += r.cost
        elif r.timestamp >= d7:
            spend_24_168 += r.cost

    return (
        spend_0_6 / 6.0,
        spend_6_24 / 18.0,
        spend_24_168 / 144.0,
    )


def _calculate_activity_density(
    records: Sequence[UsageRecord],
    now: datetime,
) -> float:
    """Active-hour density over the past 28 days (or observed window for new
    users).  A calendar-hour bucket is "active" if it contains ≥1 record."""
    if not records:
        return DEFAULT_ACTIVITY_DENSITY

    observed_start = min(r.timestamp for r in records)
    observed_hours = min(
        max((now - observed_start).total_seconds() / 3600.0, 1.0),
        28 * 24,  # 672 hours cap
    )

    lookback = now - timedelta(days=28)
    active_hours: set[tuple[int, int, int, int]] = set()
    for r in records:
        if r.timestamp >= lookback:
            active_hours.add((
                r.timestamp.year, r.timestamp.month,
                r.timestamp.day, r.timestamp.hour,
            ))

    density = len(active_hours) / observed_hours if observed_hours > 0 else DEFAULT_ACTIVITY_DENSITY
    return clamp(density, 0.03, 1.0)


def _calculate_burst_reference(
    records: Sequence[UsageRecord],
    now: datetime,
) -> float:
    """75th percentile of 5-hour block spend over the past 28 days.

    Used instead of ``recent_rate`` to size the 5-hour burst allowance, so
    the adjustment reflects the user's *typical peak usage* rather than their
    *current burn rate* — avoiding the feedback loop where hitting the limit
    increases the limit.
    """
    if not records:
        return 0.0

    lookback = now - timedelta(days=28)
    recent = [r for r in records if r.timestamp >= lookback]
    if len(recent) < 2:
        return sum(r.cost for r in recent) if recent else 0.0

    recent.sort(key=lambda r: r.timestamp)
    # Divide the observation window into 5-hour blocks and sum each
    block_start = recent[0].timestamp
    blocks: list[float] = []
    idx = 0
    while idx < len(recent):
        block_end = block_start + timedelta(hours=5)
        s = 0.0
        while idx < len(recent) and recent[idx].timestamp < block_end:
            s += recent[idx].cost
            idx += 1
        blocks.append(s)
        block_start = block_end

    if not blocks:
        return 0.0

    blocks.sort()
    p75 = blocks[int(len(blocks) * 0.75)]
    return p75


def _monthly_next_refresh(now: datetime, start_day: int = 1) -> datetime:
    """Start of the next billing period after *now*."""
    y, m = now.year, now.month
    if start_day == 1:
        if m == 12:
            return datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        return datetime(y, m + 1, 1, tzinfo=timezone.utc)
    import calendar
    # This month's period start
    _, days_this = calendar.monthrange(y, m)
    this_start = datetime(y, m, min(start_day, days_this), tzinfo=timezone.utc)
    if now >= this_start:
        # Already in this period → next period is next month
        nm = m + 1 if m < 12 else 1
        ny = y if m < 12 else y + 1
    else:
        # Haven't reached this month's start yet → period is this month
        nm = m
        ny = y
    _, days_next = calendar.monthrange(ny, nm)
    return datetime(ny, nm, min(start_day, days_next), tzinfo=timezone.utc)


def _estimate_refresh_at(
    records: Sequence[UsageRecord],
    now: datetime,
    budget: float,
    window_hours: float,
) -> str:
    """Return ISO timestamp of when rolling *window_hours* spend will drop
    below *budget*.  Returns empty string if already within budget."""
    if budget <= 0 or not records:
        return ""

    lookback = now - timedelta(hours=window_hours)
    window_records = sorted(
        [r for r in records if r.timestamp >= lookback],
        key=lambda r: r.timestamp,
    )
    total = sum(r.cost for r in window_records)
    if total < budget - 1e-9:
        return ""

    # Walk from oldest to newest; when cumulative dropped cost brings
    # remaining spent under budget, that record's age gives the wait time.
    dropped = 0.0
    for r in window_records:
        dropped += r.cost
        if total - dropped <= budget:
            age = (now - r.timestamp).total_seconds()
            wait = max(window_hours * 3600 - age, 0)
            return (now + timedelta(seconds=wait)).isoformat()

    return (now + timedelta(hours=window_hours)).isoformat()


# ---------------------------------------------------------------------------
# Core controller
# ---------------------------------------------------------------------------

class AdaptiveBudgetController:
    """Deterministic budget window calculator.

    Usage::

        controller = AdaptiveBudgetController()
        state = controller.calculate(
            monthly_budget=100.0,
            monthly_spent=40.0,
            usage_records=[...],
            previous_state=prev,   # or None for fresh start
            now=datetime.now(timezone.utc),
        )
    """

    def calculate(
        self,
        monthly_budget: float,
        monthly_spent: float,
        usage_records: Sequence[UsageRecord],
        previous_state: BudgetState | None,
        now: datetime,
        start_day: int = 1,
    ) -> BudgetState:
        remaining_budget = max(monthly_budget - monthly_spent, 0.0)

        # ── Rates (non-overlapping time buckets) ──
        rate_0_6, rate_6_24, rate_24_168 = _calculate_rates(usage_records, now)
        recent_rate = 0.50 * rate_0_6 + 0.30 * rate_6_24 + 0.20 * rate_24_168

        # ── Remaining hours & base rate ──
        remaining_hours = _remaining_hours_in_month(now, start_day)
        base_rate = (
            remaining_budget * RESERVE_FACTOR / max(remaining_hours, 1.0)
        )

        pressure = recent_rate / max(base_rate, EPSILON)

        # ── Activity density ──
        activity_density = _calculate_activity_density(usage_records, now)

        # ── Weekly target ──
        weekly_base = base_rate * 168.0
        # Negative exponent: heavy usage → tighter weekly cap (conservative at week level)
        weekly_adjust = clamp(max(pressure, EPSILON) ** -0.25, 0.85, 2.50)
        weekly_target = weekly_base * weekly_adjust
        weekly_target = min(weekly_target, remaining_budget)  # hard cap

        # ── Five-hour target ──
        # Square‑root density scaling: smooths out the low‑density explosion
        # that 1/density produces.
        nominal_5h = weekly_target * 5.0 / 168.0
        density_adjust = clamp(
            (DEFAULT_ACTIVITY_DENSITY / max(activity_density, EPSILON)) ** 0.5,
            0.85,
            1.80,
        )
        five_hour_base = nominal_5h * density_adjust
        five_hour_base = clamp(
            five_hour_base,
            weekly_target * 0.18,
            weekly_target * 0.35,
        )
        # Historical burst profile (75th‑percentile 5h‑block spend) replaces
        # ``recent_rate * 5`` so that hitting the limit doesn't become
        # evidence for raising the limit.
        burst_reference = _calculate_burst_reference(usage_records, now)
        burst_pressure = burst_reference / max(five_hour_base, EPSILON)
        burst_adjust = clamp(max(burst_pressure, EPSILON) ** 0.20, 0.85, 1.60)
        five_hour_target = five_hour_base * burst_adjust

        # weekly_spent is cost in last 7 days — use it for remaining cap
        weekly_spent = _usage_in_window(usage_records, now - timedelta(days=7))
        weekly_remaining = max(weekly_target - weekly_spent, 0.0)

        five_hour_target = min(
            five_hour_target,
            max(weekly_remaining, 0.0) * 0.70,
            remaining_budget * 0.40,
        )

        # ── Exhausted budget guard ──
        if remaining_budget <= 0:
            weekly_target = 0.0
            five_hour_target = 0.0

        # ── Smooth (EWMA + change limit) ──
        if previous_state is not None and previous_state.monthly_budget > 0:
            # Skip smoothing when the user explicitly changed the monthly
            # budget (e.g. ¥50 → ¥200), so windows adapt immediately.
            if abs(monthly_budget - previous_state.monthly_budget) / max(previous_state.monthly_budget, 1) > 0.2:
                weekly_budget = weekly_target
                five_hour_budget = five_hour_target
            else:
                weekly_budget = self._smooth_weekly(
                    previous_state.weekly_budget, weekly_target,
                )
                five_hour_budget = self._smooth_five_hour(
                    previous_state.five_hour_budget, five_hour_target,
                )
        else:
            # First-ever calculation — no smoothing
            weekly_budget = weekly_target
            five_hour_budget = five_hour_target

        # ── Final hard caps (re-apply after smoothing) ──
        weekly_budget = min(weekly_budget, remaining_budget)
        five_hour_budget = min(
            five_hour_budget,
            max(weekly_budget - weekly_spent, 0.0) * 0.70,
            remaining_budget * 0.40,
        )

        # ── Active‑limit floor (must be AFTER hard caps) ──
        # Prevents retroactive blocking: if the caps reduce a window budget
        # below what the user has already spent, the floor brings it back up
        # so ``remaining = budget - spent >= 0``.
        five_hour_spent = _usage_in_window(usage_records, now - timedelta(hours=5))
        if remaining_budget > 0:
            weekly_budget = max(weekly_budget, weekly_spent)
            five_hour_budget = max(five_hour_budget, five_hour_spent)

        # ── Minimum floor: guarantee at least 25% of the original monthly
        # budget per week (but no more than what's left).  This prevents the
        # pressure-adjuster from grinding the weekly budget below a useful
        # minimum — the user can always spend at least a quarter of their
        # monthly allowance in any given week, giving them real flexibility
        # even under sustained heavy usage.
        weekly_budget = max(weekly_budget, min(monthly_budget * 0.25, remaining_budget))

        # ── Build state ──

        monthly_refresh = _monthly_next_refresh(now, start_day).isoformat()

        weekly_refresh_at = _estimate_refresh_at(usage_records, now, weekly_budget, 168.0)
        five_hour_refresh_at = _estimate_refresh_at(usage_records, now, five_hour_budget, 5.0)

        return BudgetState(
            monthly_budget=monthly_budget,
            monthly_spent=monthly_spent,
            weekly_budget=weekly_budget,
            weekly_spent=weekly_spent,
            five_hour_budget=five_hour_budget,
            five_hour_spent=five_hour_spent,
            base_rate=base_rate,
            recent_rate=recent_rate,
            pressure=pressure,
            activity_density=activity_density,
            weekly_target_raw=weekly_target,
            five_hour_target_raw=five_hour_target,
            last_recalculated_at=now.isoformat(),
            start_day=start_day,
            five_hour_next_refresh_at=five_hour_refresh_at,
            weekly_next_refresh_at=weekly_refresh_at,
            monthly_next_refresh_at=monthly_refresh,
        )

    # ------------------------------------------------------------------
    # Smoothing
    # ------------------------------------------------------------------

    @staticmethod
    def _smooth_weekly(old: float, target: float) -> float:
        smoothed = old * (1.0 - WEEKLY_ALPHA) + target * WEEKLY_ALPHA
        return apply_change_limit(
            old, smoothed,
            WEEKLY_CHANGE_MAX_DECREASE,
            WEEKLY_CHANGE_MAX_INCREASE,
        )

    @staticmethod
    def _smooth_five_hour(old: float, target: float) -> float:
        smoothed = old * (1.0 - FIVE_HOUR_ALPHA) + target * FIVE_HOUR_ALPHA
        return apply_change_limit(
            old, smoothed,
            FIVE_HOUR_CHANGE_MAX_DECREASE,
            FIVE_HOUR_CHANGE_MAX_INCREASE,
        )

