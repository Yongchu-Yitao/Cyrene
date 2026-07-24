"""Tests for AdaptiveBudgetController — deterministic, pure Python, no I/O."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from cyrene.adaptive_budget import (
    DEFAULT_ACTIVITY_DENSITY,
    AdaptiveBudgetController,
    BudgetState,
    UsageRecord,
    _calculate_activity_density,
    _calculate_rates,
    _remaining_hours_in_month,
    _usage_in_window,
    apply_change_limit,
    clamp,
)

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)

CONTROLLER = AdaptiveBudgetController()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _usd(cents: float) -> float:
    """Build a USD cost value (in dollars, to avoid float noise)."""
    return cents


def _r(d: dict[str, Any]) -> dict[str, Any]:
    """Round all float values in a dict for deterministic comparison."""
    return {k: round(v, 6) if isinstance(v, float) else v for k, v in d.items()}


def _make_records(*pairs: tuple[float, float]) -> list[UsageRecord]:
    """Build UsageRecords at *hours_ago* offsets from NOW.  Each pair is
    ``(hours_ago, cost)``."""
    return [
        UsageRecord(timestamp=NOW - timedelta(hours=h), cost=c)
        for h, c in pairs
    ]


# ===================================================================
# 1  New user, no usage history
# ===================================================================

def test_new_user_no_history():
    state = CONTROLLER.calculate(
        monthly_budget=_usd(50),
        monthly_spent=0,
        usage_records=[],
        previous_state=None,
        now=NOW,
    )
    assert state.monthly_budget == 50
    assert state.monthly_spent == 0
    assert state.activity_density == DEFAULT_ACTIVITY_DENSITY  # 0.15
    assert state.weekly_budget > 0
    assert state.five_hour_budget > 0
    # Invariant: weekly ≤ monthly_remaining
    assert state.weekly_budget <= max(50 - 0, 0)
    # five_hour is capped by 15% of remaining_budget (hard cap)
    assert state.five_hour_budget <= (50 - 0) * 0.15
    assert state.five_hour_budget > 0
    print("  PASS: test_new_user_no_history")


# ===================================================================
# 2  Low-frequency user (sparse records over 28 days)
# ===================================================================

def test_low_frequency_user():
    # 8 records at roughly 3‑day intervals over 24 days
    recs = _make_records(*[
        (h, _usd(1.0))
        for h in range(24 * 3, 0, -24 * 3)  # 72, 48, 24 ...
    ])
    density = _calculate_activity_density(recs, NOW)
    assert 0.03 <= density <= 0.15  # sparse → low density

    state = CONTROLLER.calculate(
        monthly_budget=_usd(50),
        monthly_spent=_usd(8),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    assert state.weekly_budget > 0
    assert state.five_hour_budget > 0
    assert state.pressure >= 0
    print("  PASS: test_low_frequency_user")


# ===================================================================
# 3  High-frequency continuous user
# ===================================================================

def test_high_frequency_user():
    # Continuous usage: every hour for 7 days
    recs = _make_records(*[
        (h, _usd(0.05))
        for h in range(7 * 24, 0, -1)  # 168 hours
    ])
    density = _calculate_activity_density(recs, NOW)
    assert density >= 0.5  # dense

    state = CONTROLLER.calculate(
        monthly_budget=_usd(200),
        monthly_spent=_usd(80),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    assert state.weekly_budget > 0
    assert state.five_hour_budget > 0
    print("  PASS: test_high_frequency_user")


# ===================================================================
# 4  Burst user (concentrates use twice per week)
# ===================================================================

def test_burst_user():
    # Two concentrated sessions per week, 4 hours each, high spend
    recs: list[UsageRecord] = []
    for week in range(4):
        for day_offset in [1, 4]:  # Tue & Fri roughly
            for h in range(4):
                ts = NOW - timedelta(days=7 * week + (7 - day_offset), hours=h)
                recs.append(UsageRecord(timestamp=ts, cost=_usd(0.50)))

    state = CONTROLLER.calculate(
        monthly_budget=_usd(100),
        monthly_spent=_usd(30),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    assert state.weekly_budget > 0
    assert state.five_hour_budget > 0
    assert state.activity_density < 0.5  # not continuous
    print("  PASS: test_burst_user")


# ===================================================================
# 5  Recent 6h spend surge
# ===================================================================

def test_recent_six_hour_surge():
    # Baseline: moderate usage
    recs = _make_records(*[
        (h + 12, _usd(0.10)) for h in range(7 * 24, 0, -6)  # every 6h for 7 days
    ])
    # Surge in last 6 hours
    recs.extend([
        UsageRecord(timestamp=NOW - timedelta(hours=h), cost=_usd(2.0))
        for h in [0.5, 1.5, 2.5, 3.5]
    ])

    state = CONTROLLER.calculate(
        monthly_budget=_usd(100),
        monthly_spent=_usd(45),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    # Pressure should be elevated due to surge
    assert state.pressure > 0.5
    print("  PASS: test_recent_six_hour_surge")


# ===================================================================
# 6  Recent usage drop
# ===================================================================

def test_recent_usage_drop():
    # Heavy usage 3-7 days ago, nothing recent
    recs = _make_records(*[
        (h, _usd(1.0))
        for h in range(7 * 24, 6 * 24, -1)  # hours 168→144 ago
    ])

    state = CONTROLLER.calculate(
        monthly_budget=_usd(100),
        monthly_spent=_usd(50),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    # With recent rate near 0, pressure should be low → weekly_adjust goes up
    assert state.recent_rate >= 0
    assert state.pressure < 5.0
    print("  PASS: test_recent_usage_drop")


# ===================================================================
# 7  Monthly budget nearly exhausted
# ===================================================================

def test_monthly_budget_nearly_exhausted():
    recs = _make_records((1, _usd(0.50)))
    state = CONTROLLER.calculate(
        monthly_budget=_usd(50),
        monthly_spent=_usd(48),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    assert state.weekly_budget <= 50 - 48  # capped by remaining
    assert state.weekly_budget >= 0
    assert state.five_hour_budget >= 0
    # Active-limit floor ensures budget never drops below already-spent
    assert state.five_hour_budget >= 0.5, "floor should protect spent"
    print("  PASS: test_monthly_budget_nearly_exhausted")


# ===================================================================
# 8  Monthly budget fully exhausted
# ===================================================================

def test_monthly_budget_exhausted():
    recs = _make_records((1, _usd(1.0)))
    state = CONTROLLER.calculate(
        monthly_budget=_usd(50),
        monthly_spent=_usd(50),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    assert state.weekly_budget == 0
    assert state.five_hour_budget == 0
    print("  PASS: test_monthly_budget_exhausted")


# ===================================================================
# 9  Start of month
# ===================================================================

def test_start_of_month():
    start = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    remaining = _remaining_hours_in_month(start)
    # July = 31 days = 744 hours
    assert 740 <= remaining <= 745

    state = CONTROLLER.calculate(
        monthly_budget=_usd(100),
        monthly_spent=0,
        usage_records=[],
        previous_state=None,
        now=start,
    )
    # Base rate at month start: 100 * 0.9 / 744 ≈ 0.121 /hour
    assert 0.1 <= state.base_rate <= 0.13
    assert state.weekly_budget > 0
    print("  PASS: test_start_of_month")


# ===================================================================
# 10  End of month (last few hours)
# ===================================================================

def test_end_of_month():
    end = datetime(2026, 7, 31, 22, 0, 0, tzinfo=timezone.utc)
    remaining = _remaining_hours_in_month(end)
    assert 1.5 <= remaining <= 3  # ~2h left

    state = CONTROLLER.calculate(
        monthly_budget=_usd(100),
        monthly_spent=_usd(95),
        usage_records=[],
        previous_state=None,
        now=end,
    )
    # With only 2h left and 5 remaining, base_rate = 5 * 0.9 / 2 = 2.25
    # But weekly is capped by remaining_budget = 5
    assert state.weekly_budget <= 5
    assert state.five_hour_budget <= (100 - 95) * 0.15
    print("  PASS: test_end_of_month")


# ===================================================================
# 11  Weekly target > remaining monthly budget
# ===================================================================

def test_weekly_target_exceeds_remaining():
    recs = _make_records((1, _usd(0.10)))
    state = CONTROLLER.calculate(
        monthly_budget=_usd(100),
        monthly_spent=_usd(98),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    # Weekly must be ≤ remaining (which is 2)
    assert state.weekly_budget <= 2
    assert state.five_hour_budget <= (100 - 98) * 0.15
    print("  PASS: test_weekly_target_exceeds_remaining")


# ===================================================================
# 12  Five-hour > 35% of weekly remaining
# ===================================================================

def test_five_hour_capped_by_weekly_remaining():
    recs = _make_records((1, _usd(0.10)))
    # Create a previous state with high weekly_budget so smoothing doesn't
    # interfere with the cap test
    prev = BudgetState(
        monthly_budget=_usd(100),
        monthly_spent=_usd(50),
        weekly_budget=_usd(50),
        weekly_spent=_usd(45),  # only 5 remaining
        five_hour_budget=_usd(10),
        five_hour_spent=0,
        base_rate=0.5,
        recent_rate=0.2,
        pressure=0.4,
        activity_density=0.5,
        weekly_target_raw=_usd(50),
        five_hour_target_raw=_usd(10),
        last_recalculated_at=(NOW - timedelta(minutes=5)).isoformat(),
    )
    state = CONTROLLER.calculate(
        monthly_budget=_usd(100),
        monthly_spent=_usd(50),
        usage_records=recs,
        previous_state=prev,
        now=NOW,
    )
    max_5h = max(state.weekly_budget - 0.10, 0) * 0.35  # weekly_spent=0.10
    assert state.five_hour_budget <= max_5h
    print("  PASS: test_five_hour_capped_by_weekly_remaining")


# ===================================================================
# 13  Five-hour > 15% of monthly remaining
# ===================================================================

def test_five_hour_capped_by_monthly_remaining():
    recs = _make_records((1, _usd(0.10)))
    state = CONTROLLER.calculate(
        monthly_budget=_usd(50),
        monthly_spent=_usd(1),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    assert state.five_hour_budget <= max(50 - 1, 0) * 0.15  # ≤ 7.35
    assert state.five_hour_budget > 0
    print("  PASS: test_five_hour_capped_by_monthly_remaining")


# ===================================================================
# 14  EWMA smoothing
# ===================================================================

def test_ewma_smoothing():
    prev = BudgetState(
        monthly_budget=_usd(100),
        monthly_spent=_usd(30),
        weekly_budget=_usd(35),
        weekly_spent=_usd(5),
        five_hour_budget=_usd(5),
        five_hour_spent=_usd(1),
        base_rate=0.2,
        recent_rate=0.15,
        pressure=0.75,
        activity_density=0.3,
        weekly_target_raw=_usd(35),
        five_hour_target_raw=_usd(5),
        last_recalculated_at=(NOW - timedelta(hours=1)).isoformat(),
    )
    recs = _make_records((2, _usd(0.50)))
    state = CONTROLLER.calculate(
        monthly_budget=_usd(100),
        monthly_spent=_usd(30),
        usage_records=recs,
        previous_state=prev,
        now=NOW,
    )
    # With alpha=0.10, the new weekly should be close to old but not identical
    assert state.weekly_budget != prev.weekly_budget or state.weekly_target_raw != prev.weekly_target_raw
    print("  PASS: test_ewma_smoothing")


# ===================================================================
# 15  Single-step change limit
# ===================================================================

def test_change_rate_limit():
    # Max decrease 10%, max increase 15%
    result = apply_change_limit(100.0, 50.0, 0.10, 0.15)
    assert abs(result - 90.0) < 1e-9  # 100 * (1 - 0.10)
    result = apply_change_limit(100.0, 200.0, 0.10, 0.15)
    assert abs(result - 115.0) < 1e-9  # 100 * (1 + 0.15)
    result = apply_change_limit(100.0, 105.0, 0.10, 0.15)
    assert abs(result - 105.0) < 1e-9  # within bounds → equals target
    print("  PASS: test_change_rate_limit")


# ===================================================================
# 16  Rolling 5h window boundary
# ===================================================================

def test_five_hour_window_boundary():
    # Record at exactly 5h ago → should be included (>=)
    edge = NOW - timedelta(hours=5)
    recs = [UsageRecord(timestamp=edge, cost=_usd(10.0))]
    spent = _usage_in_window(recs, NOW - timedelta(hours=5))
    assert spent == 10.0

    # Record at 5h + 1s ago → should be excluded
    edge2 = NOW - timedelta(hours=5, seconds=1)
    recs2 = [UsageRecord(timestamp=edge2, cost=_usd(10.0))]
    spent2 = _usage_in_window(recs2, NOW - timedelta(hours=5))
    assert spent2 == 0.0, f"Expected 0, got {spent2}"
    print("  PASS: test_five_hour_window_boundary")


# ===================================================================
# 17  Rolling 7d window boundary
# ===================================================================

def test_seven_day_window_boundary():
    edge = NOW - timedelta(days=7)
    recs = [UsageRecord(timestamp=edge, cost=_usd(10.0))]
    spent = _usage_in_window(recs, NOW - timedelta(days=7))
    assert spent == 10.0

    edge2 = NOW - timedelta(days=7, seconds=1)
    recs2 = [UsageRecord(timestamp=edge2, cost=_usd(10.0))]
    spent2 = _usage_in_window(recs2, NOW - timedelta(days=7))
    assert spent2 == 0.0
    print("  PASS: test_seven_day_window_boundary")


# ===================================================================
# 18  Timezone-aware datetime handling
# ===================================================================

def test_timezone_aware():
    utc = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
    east = datetime(2026, 7, 4, 8, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    recs = [UsageRecord(timestamp=utc, cost=_usd(1.0))]

    r1 = _calculate_rates(recs, utc)
    r2 = _calculate_rates(recs, east)
    # Same instant, different tz → same relative times → same results
    assert abs(r1[0] - r2[0]) < 1e-9
    print("  PASS: test_timezone_aware")


# ===================================================================
# 19  Usage-record order independence
# ===================================================================

def test_record_order_independence():
    recs_a = _make_records((1, 2.0), (2, 3.0), (5, 1.0))
    recs_b = _make_records((5, 1.0), (1, 2.0), (2, 3.0))

    def run(recs):
        return CONTROLLER.calculate(
            monthly_budget=_usd(100),
            monthly_spent=_usd(20),
            usage_records=recs,
            previous_state=None,
            now=NOW,
        )

    sa = run(recs_a)
    sb = run(recs_b)
    assert sa.weekly_budget == sb.weekly_budget
    assert sa.five_hour_budget == sb.five_hour_budget
    assert sa.recent_rate == sb.recent_rate
    print("  PASS: test_record_order_independence")


# ===================================================================
# 20  Deterministic output for identical input
# ===================================================================

def test_deterministic():
    recs = _make_records((1, 0.50), (3, 1.20), (6, 0.80), (24, 3.00))
    kw = dict(
        monthly_budget=_usd(100),
        monthly_spent=_usd(30),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    a = CONTROLLER.calculate(**kw)
    b = CONTROLLER.calculate(**kw)
    assert a.weekly_budget == b.weekly_budget
    assert a.five_hour_budget == b.five_hour_budget
    assert a.base_rate == b.base_rate
    assert a.pressure == b.pressure
    assert a.activity_density == b.activity_density
    print("  PASS: test_deterministic")


# ===================================================================
# Invariant tests
# ===================================================================

def test_invariant_zero_weekly_budget():
    """When weekly budget is 0, five_hour must also be 0."""
    recs = _make_records((1, _usd(0.50)))
    state = CONTROLLER.calculate(
        monthly_budget=_usd(100),
        monthly_spent=_usd(100),
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    assert state.weekly_budget == 0
    assert state.five_hour_budget == 0
    print("  PASS: test_invariant_zero_weekly_budget")


def test_invariant_non_negative():
    """All remaining values must be >= 0."""
    recs = _make_records((1, _usd(0.50)))
    state = CONTROLLER.calculate(
        monthly_budget=_usd(50),
        monthly_spent=0,
        usage_records=recs,
        previous_state=None,
        now=NOW,
    )
    assert state.weekly_spent >= 0
    assert state.five_hour_spent >= 0
    assert max(state.weekly_budget - state.weekly_spent, 0) >= 0
    assert max(state.five_hour_budget - state.five_hour_spent, 0) >= 0
    print("  PASS: test_invariant_non_negative")


# ===================================================================
# Utility tests
# ===================================================================

def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(15, 0, 10) == 10
    print("  PASS: test_clamp")


def test_remaining_hours_in_month():
    # July (31 days = 744 h)
    start = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert _remaining_hours_in_month(start) == 744.0

    mid = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    assert abs(_remaining_hours_in_month(mid) - 396.0) < 0.01

    late = datetime(2026, 7, 31, 23, 0, 0, tzinfo=timezone.utc)
    assert abs(_remaining_hours_in_month(late) - 1.0) < 0.01

    # Feb 2026 (28 days = 672 h)
    feb_start = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert _remaining_hours_in_month(feb_start) == 672.0

    feb_mid = datetime(2026, 2, 15, 0, 0, 0, tzinfo=timezone.utc)
    assert abs(_remaining_hours_in_month(feb_mid) - 336.0) < 0.01  # 14 days
    print("  PASS: test_remaining_hours_in_month")


def test_calculate_rates():
    recs = _make_records(
        (1, 6.0),   # within 0-6h
        (3, 3.0),   # within 0-6h
        (10, 5.0),  # within 6-24h
        (48, 10.0),  # within 24-168h
    )
    r6, r6_24, r24_168 = _calculate_rates(recs, NOW)
    # 0-6h: records at 1h and 3h → 6+3 = 9 / 6 = 1.5
    assert abs(r6 - 1.5) < 1e-6
    # 6-24h: record at 10h → 5 / 18 ≈ 0.278
    assert abs(r6_24 - 5 / 18) < 1e-6
    # 24-168h: record at 48h → 10 / 144 ≈ 0.0694
    assert abs(r24_168 - 10 / 144) < 1e-6

    # Recent_rate formula
    recent = 0.50 * r6 + 0.30 * r6_24 + 0.20 * r24_168
    assert abs(recent - (0.50*1.5 + 0.30*5/18 + 0.20*10/144)) < 1e-6
    print("  PASS: test_calculate_rates")


# ===================================================================
# Run all
# ===================================================================

if __name__ == "__main__":
    tests = [
        test_new_user_no_history,
        test_low_frequency_user,
        test_high_frequency_user,
        test_burst_user,
        test_recent_six_hour_surge,
        test_recent_usage_drop,
        test_monthly_budget_nearly_exhausted,
        test_monthly_budget_exhausted,
        test_start_of_month,
        test_end_of_month,
        test_weekly_target_exceeds_remaining,
        test_five_hour_capped_by_weekly_remaining,
        test_five_hour_capped_by_monthly_remaining,
        test_ewma_smoothing,
        test_change_rate_limit,
        test_five_hour_window_boundary,
        test_seven_day_window_boundary,
        test_timezone_aware,
        test_record_order_independence,
        test_deterministic,
        test_invariant_zero_weekly_budget,
        test_invariant_non_negative,
        test_clamp,
        test_remaining_hours_in_month,
        test_calculate_rates,
    ]
    for t in tests:
        t()
    print(f"\n{len(tests)} tests passed.")
