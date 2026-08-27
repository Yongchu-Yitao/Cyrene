"""Budget enforcement — wraps AdaptiveBudgetController with DB + persistence.

All internal computation uses the user's configured currency (CNY / USD).
``estimated_cost`` in ``token_usage`` is stored in the pricing's native
currency (CNY for all built-in models) and used directly when the user's
currency matches.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from cyrene.runtime.adaptive_budget import (
    MIN_RECALCULATION_INTERVAL_SECONDS,
    BudgetState,
    UsageRecord,
    AdaptiveBudgetController,
)
from cyrene.model_runtime.pricing import CNY_PER_USD
from cyrene.runtime.settings_store import get_all as _get_all_settings

_CONTROLLER = AdaptiveBudgetController()
_BUDGET_STATE_KEY = "budget_adaptive_state"

_BUDGET_RESPONSE_KEYS = [
    "monthly_budget", "monthly_spent", "monthly_remaining",
    "weekly_budget", "weekly_spent", "weekly_remaining",
    "five_hour_budget", "five_hour_spent", "five_hour_remaining",
    "base_rate", "recent_rate", "pressure", "activity_density",
    "monthly_budget_usd", "last_recalculated_at",
    "five_hour_next_refresh_at", "weekly_next_refresh_at", "monthly_next_refresh_at",
    "currency",
]


class BudgetUsageQueryError(RuntimeError):
    """Usage data could not be read reliably enough to enforce a budget."""


# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------

def _db_cost_to_user_currency(cny_cost: float, currency: str) -> float:
    """Convert a DB cost (CNY) to the user's configured currency.

    Built-in prices are all in CNY, so no conversion needed when the
    user also uses CNY — the common case.  For USD users, divide by
    the exchange rate.
    """
    if currency == "CNY":
        return cny_cost
    return cny_cost / CNY_PER_USD if CNY_PER_USD > 0 else cny_cost


def _to_usd(value: float, currency: str) -> float:
    """Convert a user-currency value to USD for display."""
    if currency == "USD":
        return value
    return value / CNY_PER_USD if CNY_PER_USD > 0 else value


# ---------------------------------------------------------------------------
# DB helpers (async, reuse token_usage table)
# ---------------------------------------------------------------------------

async def _query_sum(db_path: str, since: datetime) -> float:
    """Sum canonical CNY ``estimated_cost`` values since *since*."""
    import aiosqlite

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            async with aiosqlite.connect(db_path) as db:
                async with db.execute(
                    "SELECT COALESCE(SUM(estimated_cost), 0) FROM token_usage WHERE created_at >= ?",
                    [since.isoformat()],
                ) as cur:
                    row = await cur.fetchone()
                    return float(row[0]) if row else 0.0
        except Exception as exc:
            last_error = exc
            if attempt:
                break
    raise BudgetUsageQueryError("failed to read budget usage totals") from last_error


async def _query_records(
    db_path: str,
    hours: int = 31 * 24,
) -> list[UsageRecord]:
    """Fetch usage records for the last *hours* (default 28 days)."""
    import aiosqlite

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT created_at, estimated_cost FROM token_usage "
                    "WHERE created_at >= ? ORDER BY created_at",
                    [since],
                ) as cur:
                    records: list[UsageRecord] = []
                    async for row in cur:
                        try:
                            raw = row["created_at"]
                            ts = datetime.fromisoformat(raw)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            records.append(
                                UsageRecord(timestamp=ts, cost=float(row["estimated_cost"]))
                            )
                        except (ValueError, TypeError):
                            continue
                    return records
        except Exception as exc:
            last_error = exc
            if attempt:
                break
    raise BudgetUsageQueryError("failed to read budget usage records") from last_error


def _calendar_month_start(now: datetime, start_day: int = 1) -> datetime:
    """Start of the current billing period respecting ``start_day``."""
    if start_day == 1:
        return datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    import calendar
    _, days_in_month = calendar.monthrange(now.year, now.month)
    period_start = datetime(now.year, now.month, min(start_day, days_in_month), tzinfo=timezone.utc)
    if now < period_start:
        # Period started last month
        pm = now.month - 1 if now.month > 1 else 12
        py = now.year if now.month > 1 else now.year - 1
        _, days_in_prev = calendar.monthrange(py, pm)
        return datetime(py, pm, min(start_day, days_in_prev), tzinfo=timezone.utc)
    return period_start


# ---------------------------------------------------------------------------
# State persistence via settings store
# ---------------------------------------------------------------------------

def _load_state() -> BudgetState | None:
    try:
        raw = _get_all_settings().get(_BUDGET_STATE_KEY)
        if isinstance(raw, str):
            d = json.loads(raw)
            return BudgetState(**d)
        if isinstance(raw, dict):
            return BudgetState(**raw)
    except (TypeError, ValueError, KeyError):
        pass
    return None


def _save_state(state: BudgetState) -> None:
    from cyrene.runtime.settings_store import set_ as _set
    _set(_BUDGET_STATE_KEY, json.dumps({
        "monthly_budget": state.monthly_budget,
        "monthly_spent": state.monthly_spent,
        "weekly_budget": state.weekly_budget,
        "weekly_spent": state.weekly_spent,
        "five_hour_budget": state.five_hour_budget,
        "five_hour_spent": state.five_hour_spent,
        "base_rate": state.base_rate,
        "recent_rate": state.recent_rate,
        "pressure": state.pressure,
        "activity_density": state.activity_density,
        "weekly_target_raw": state.weekly_target_raw,
        "five_hour_target_raw": state.five_hour_target_raw,
        "last_recalculated_at": state.last_recalculated_at,
        "currency": state.currency,
        "start_day": state.start_day,
        "five_hour_next_refresh_at": state.five_hour_next_refresh_at,
        "weekly_next_refresh_at": state.weekly_next_refresh_at,
        "monthly_next_refresh_at": state.monthly_next_refresh_at,
        "five_hour_window_start": state.five_hour_window_start,
        "weekly_window_start": state.weekly_window_start,
    }))


# ---------------------------------------------------------------------------
# Response builder (values already in user currency, no conversion needed)
# ---------------------------------------------------------------------------

def _build_response(state: BudgetState, default_currency: str = "CNY") -> dict[str, Any]:
    effective_currency = state.currency or default_currency

    return {
        "monthly_budget": state.monthly_budget,
        "monthly_spent": state.monthly_spent,
        "monthly_remaining": max(state.monthly_budget - state.monthly_spent, 0.0),
        "weekly_budget": state.weekly_budget,
        "weekly_spent": state.weekly_spent,
        "weekly_remaining": max(state.weekly_budget - state.weekly_spent, 0.0),
        "five_hour_budget": state.five_hour_budget,
        "five_hour_spent": state.five_hour_spent,
        "five_hour_remaining": max(state.five_hour_budget - state.five_hour_spent, 0.0),
        "base_rate": state.base_rate,
        "recent_rate": state.recent_rate,
        "pressure": state.pressure,
        "activity_density": state.activity_density,
        "monthly_budget_usd": _to_usd(state.monthly_budget, effective_currency),
        "last_recalculated_at": state.last_recalculated_at,
        "five_hour_next_refresh_at": state.five_hour_next_refresh_at or "",
        "weekly_next_refresh_at": state.weekly_next_refresh_at or "",
        "monthly_next_refresh_at": state.monthly_next_refresh_at or "",
        "currency": effective_currency,
    }


# ---------------------------------------------------------------------------
# Window-start helpers (hard-reset windows)
# ---------------------------------------------------------------------------

def _parse_ws(iso_str: str) -> datetime | None:
    """Parse an ISO window_start string to datetime, or None if empty."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _window_refresh_at(ws: datetime | None, window_hours: int) -> str:
    """Return ISO timestamp of when the hard-reset window ends, or empty."""
    if not ws:
        return ""
    return (ws + timedelta(hours=window_hours)).isoformat()


def _smooth_window_budget(
    prev_budget: float,
    target_raw: float,
    remaining: float,
) -> float:
    """Smooth the budget at a hard-reset window boundary.

    Blends the previous window's budget with the new raw target (50:50) so
    high pressure from the prior period doesn't cause an abrupt drop when
    the window resets.  The blended value is then capped at *remaining*.
    """
    if prev_budget > 0:
        blended = prev_budget * 0.5 + target_raw * 0.5
    else:
        blended = target_raw
    return min(blended, remaining)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_budget_state(
    db_path: str,
    *,
    monthly: float = 0.0,
    enabled: bool = False,
    start_windows: bool = False,
) -> dict[str, Any]:
    """Return current adaptive budget state in the user's configured currency.

    ``monthly`` should be the raw value from the user's ``budget_monthly``
    setting (in their configured currency).  All internal computation uses
    the user's currency directly.
    """
    settings = _get_all_settings()
    currency = str(settings.get("budget_currency") or "CNY").upper()
    start_day = int(settings.get("budget_start_day") or 1)
    conv = _db_cost_to_user_currency

    import math
    if not enabled or monthly <= 0 or not math.isfinite(monthly):
        return _disabled_response()

    now = datetime.now(timezone.utc)
    prev = _load_state()
    monthly_start = _calendar_month_start(now, start_day)

    # Persisted monetary values and window boundaries are meaningful only in
    # the currency and billing-cycle configuration that created them.  A
    # configuration change forces recalculation and starts fresh sub-windows
    # instead of carrying timestamps (and therefore spend) across units or
    # billing periods.
    compatible_prev = (
        prev
        if prev is not None
        and prev.currency == currency
        and getattr(prev, "start_day", 1) == start_day
        else None
    )

    # Hard-reset windows: spent is sum since window_start, not rolling window.
    # Window expires at window_start + window_hours → spent resets to 0.
    five_hour_ws = (
        _parse_ws(compatible_prev.five_hour_window_start)
        if compatible_prev else None
    )
    weekly_ws = (
        _parse_ws(compatible_prev.weekly_window_start)
        if compatible_prev else None
    )

    # Check window expiry — treat spent as 0 if window has timed out.
    if five_hour_ws and now >= five_hour_ws + timedelta(hours=5):
        five_hour_ws = None
    if weekly_ws and now >= weekly_ws + timedelta(days=7):
        weekly_ws = None

    needs_recalc = _needs_recalculation(prev, monthly, now, currency, start_day, monthly_start)

    if not needs_recalc and prev is not None:
        # Cached state — refresh spent values from DB using window_start.
        monthly_spent = await _query_sum(db_path, monthly_start)
        weekly_spent = await _query_sum(db_path, weekly_ws) if weekly_ws else 0.0
        five_hour_spent = await _query_sum(db_path, five_hour_ws) if five_hour_ws else 0.0

        prev.monthly_spent = conv(monthly_spent, currency)
        prev.weekly_spent = conv(weekly_spent, currency)
        prev.five_hour_spent = conv(five_hour_spent, currency)
        prev.currency = currency
        # Recalculate sub-window budgets ONLY when no active window (i.e.
        # setting the budget for the NEXT window).  The current window's
        # budget is fixed when the window starts — recalculating it on every
        # API call would make it drift over time (remaining_budget changes,
        # remaining_hours decays), confusing the user.
        remaining = monthly - conv(monthly_spent, currency)
        if not weekly_ws and remaining > 0 and prev.weekly_target_raw > 0:
            prev.weekly_budget = _smooth_window_budget(
                prev.weekly_budget, prev.weekly_target_raw, remaining,
            )
        if not five_hour_ws and remaining > 0 and prev.five_hour_target_raw > 0:
            prev.five_hour_budget = min(
                prev.five_hour_target_raw,
                remaining * 0.40,
                max(prev.weekly_budget - prev.weekly_spent, 0.0) * 0.70,
            )
        changed = False
        if start_windows and remaining > 0:
            if not five_hour_ws and prev.five_hour_budget > 0:
                prev.five_hour_window_start = now.isoformat()
                five_hour_ws = now
                changed = True
            if not weekly_ws and prev.weekly_budget > 0:
                prev.weekly_window_start = now.isoformat()
                weekly_ws = now
                changed = True
        # Keep the derived timestamps consistent with any windows started by
        # this call.  Otherwise the first admitted request persists active
        # window_start values alongside empty refresh_at values.
        prev.five_hour_next_refresh_at = _window_refresh_at(five_hour_ws, 5)
        prev.weekly_next_refresh_at = _window_refresh_at(weekly_ws, 168)
        if changed:
            _save_state(prev)
        return _build_response(prev)

    # Full recalculation — fetch records (CNY from DB), cap to billing period,
    # then convert all costs to the user's chosen currency ONCE.
    records = await _query_records(db_path)
    records = [r for r in records if r.timestamp >= monthly_start]
    for r in records:
        r.cost = conv(r.cost, currency)
    monthly_spent = sum(r.cost for r in records)

    # Compute sub-window spent from window_start (costs already in user currency)
    five_hour_spent = sum(r.cost for r in records if r.timestamp >= five_hour_ws) if five_hour_ws else 0.0
    weekly_spent = sum(r.cost for r in records if r.timestamp >= weekly_ws) if weekly_ws else 0.0

    try:
        state = _CONTROLLER.calculate(
            monthly_budget=monthly,
            monthly_spent=monthly_spent,
            usage_records=records,
            previous_state=compatible_prev,
            now=now,
            start_day=start_day,
        )
        state.currency = currency
        # Carry over hard-reset window_start from prev (controller creates a
        # fresh BudgetState that doesn't know about window_start).
        if compatible_prev:
            state.five_hour_window_start = compatible_prev.five_hour_window_start
            state.weekly_window_start = compatible_prev.weekly_window_start
        # Override sub-window spent & refresh_at with hard-reset window values
        state.weekly_spent = weekly_spent
        state.five_hour_spent = five_hour_spent
        # Freeze the current 5‑hour window budget — it was set when the
        # window started and must not drift on each ~60s recalculation
        # (the controller's EWMA + active-limit floor would otherwise
        # inflate it).  Weekly is intentionally NOT frozen so it adapts
        # continuously.
        if five_hour_ws and compatible_prev:
            state.five_hour_budget = compatible_prev.five_hour_budget
        # Recalculate budgets for INACTIVE windows (next window's budget).
        remaining = monthly - monthly_spent
        if not weekly_ws and state.weekly_target_raw > 0:
            state.weekly_budget = _smooth_window_budget(
                compatible_prev.weekly_budget if compatible_prev else 0.0,
                state.weekly_target_raw,
                remaining,
            )
        if not five_hour_ws and state.five_hour_target_raw > 0:
            state.five_hour_budget = min(
                state.five_hour_target_raw,
                remaining * 0.40,
                max(state.weekly_budget - weekly_spent, 0.0) * 0.70,
            )
    except Exception as exc:
        # A cached fallback is safe only for the exact configured limit.  If
        # the user just lowered the monthly budget, returning the old state's
        # larger limit could admit spending that should now be blocked.
        if compatible_prev is not None and compatible_prev.monthly_budget == monthly:
            prev.monthly_spent = conv(
                await _query_sum(db_path, monthly_start), currency,
            )
            prev.weekly_spent = conv(
                await _query_sum(db_path, weekly_ws) if weekly_ws else 0.0, currency,
            )
            prev.five_hour_spent = conv(
                await _query_sum(db_path, five_hour_ws) if five_hour_ws else 0.0, currency,
            )
            prev.currency = currency
            prev.five_hour_next_refresh_at = _window_refresh_at(five_hour_ws, 5)
            prev.weekly_next_refresh_at = _window_refresh_at(weekly_ws, 168)
            return _build_response(prev)
        raise BudgetUsageQueryError("failed to calculate budget state") from exc

    # Only a request passing through the budget gate starts a hard-reset
    # window. Status reads calculate and persist the state without consuming
    # wall-clock time from either window.
    if start_windows and monthly - monthly_spent > 0:
        if not five_hour_ws and state.five_hour_budget > 0:
            state.five_hour_window_start = now.isoformat()
            five_hour_ws = now
        if not weekly_ws and state.weekly_budget > 0:
            state.weekly_window_start = now.isoformat()
            weekly_ws = now

    state.five_hour_next_refresh_at = _window_refresh_at(five_hour_ws, 5)
    state.weekly_next_refresh_at = _window_refresh_at(weekly_ws, 168)

    _save_state(state)
    return _build_response(state)


async def check_budget_and_block(db_path: str, monthly: float, enabled: bool) -> dict | None:
    """Check budget and describe an exhausted or unavailable limit.

    ``"block"`` results stop the request. ``"warn"`` results carry
    ``warning=True`` so the caller can notify the user while allowing it.

    When a request passes, hard-reset windows that aren't yet active are
    started (window_start = now) so the first request after a reset begins
    the window timer.
    """
    if not enabled or monthly <= 0:
        return None
    settings = _get_all_settings()
    action = str(settings.get("budget_action") or "block").strip().lower()
    try:
        state = await get_budget_state(
            db_path,
            monthly=monthly,
            enabled=enabled,
            start_windows=True,
        )
    except BudgetUsageQueryError:
        result = {
            "code": "budget_usage_unavailable",
            "message": "Budget usage could not be verified. Please retry shortly.",
        }
    else:
        weekly_remaining = max(state.get("weekly_remaining", 0), 0)
        five_hour_remaining = max(state.get("five_hour_remaining", 0), 0)
        monthly_remaining = max(state.get("monthly_remaining", 0), 0)
        if monthly_remaining <= 0:
            result = {"code": "budget_monthly_exhausted", "message": "Monthly budget exhausted — reduce spending or increase your monthly limit."}
        elif weekly_remaining <= 0:
            result = {"code": "budget_weekly_exhausted", "message": "Weekly budget exhausted — reduce spending or increase your monthly limit."}
        elif five_hour_remaining <= 0:
            result = {"code": "budget_5h_exhausted", "message": "5-hour budget exhausted — usage is too concentrated, please wait before sending more requests."}
        else:
            return None
    if action == "warn":
        result["warning"] = True
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _disabled_response() -> dict[str, Any]:
    resp = {k: 0 for k in _BUDGET_RESPONSE_KEYS}
    resp["last_recalculated_at"] = ""
    return resp


def _needs_recalculation(
    prev: BudgetState | None,
    monthly: float,
    now: datetime,
    currency: str,
    start_day: int = 1,
    monthly_start: datetime | None = None,
) -> bool:
    if prev is None:
        return True
    if prev.monthly_budget != monthly:
        return True
    if prev.currency != currency:
        return True
    if getattr(prev, 'start_day', 1) != start_day:
        return True
    if monthly_start is not None:
        try:
            prev_period = _calendar_month_start(
                datetime.fromisoformat(prev.last_recalculated_at),
                getattr(prev, "start_day", 1),
            )
            if prev_period != monthly_start:
                return True
        except (ValueError, TypeError):
            return True
    # Force recalculation when either window is exhausted so the system
    # actively tries to recover rather than waiting 60 s.
    if (prev.weekly_budget <= 0 or prev.five_hour_budget <= 0) and monthly > 0:
        return True
    try:
        last = datetime.fromisoformat(prev.last_recalculated_at)
        elapsed = (now - last).total_seconds()
        if elapsed >= MIN_RECALCULATION_INTERVAL_SECONDS:
            return True
    except (ValueError, TypeError):
        return True
    return False
