from __future__ import annotations

import pytest

from cyrene import db
from cyrene.model_prices import (
    effective_price,
    estimate_cost,
    lookup_price,
    parse_user_price,
    price_hint,
    to_usd,
)


def test_lookup_price_matches_model_alias_without_short_substring_false_positive():
    assert lookup_price("") is None
    assert lookup_price("o") is None
    assert lookup_price("provider/deepseek-v4-flash-202606") == {
        "input": 1.0,
        "output": 2.0,
        "cache_hit": 0.02,
        "currency": "CNY",
    }
    assert lookup_price("gpt-5.5")["input"] == pytest.approx(36.25)
    assert lookup_price("gpt-4.1") is None
    assert lookup_price("gemini-2.5-pro") is None
    assert lookup_price("qwen3") is None


def test_only_current_claude_versions_are_built_in():
    assert lookup_price("claude-fable-5")["input"] == pytest.approx(72.5)
    assert lookup_price("claude-mythos-5")["output"] == pytest.approx(362.5)
    assert lookup_price("claude-opus-4-8") is None


def test_price_hint_and_user_price_parsing():
    assert price_hint("deepseek-v4-flash") == "¥1/2/0.02"
    assert parse_user_price("3/15/0.3") == {
        "input": 3.0,
        "output": 15.0,
        "cache_hit": 0.3,
        "currency": "CNY",
    }
    assert parse_user_price("¥2/8") == {"input": 2.0, "output": 8.0, "currency": "CNY"}
    assert parse_user_price("-1/2") is None
    assert parse_user_price("nan/2") is None
    assert parse_user_price("1/2/3/4") is None


def test_explicit_user_price_overrides_built_in(monkeypatch):
    monkeypatch.setattr("cyrene.model_prices.configured_user_price", lambda model: None)
    assert effective_price("gpt-5.5", "9/10") == {
        "input": 9.0,
        "output": 10.0,
        "currency": "CNY",
    }


@pytest.mark.parametrize(
    ("model", "expected_hint"),
    [
        ("glm-5.2", "¥8/28/2"),
        ("MiniMax-M3", "¥2.1/8.4/0.42"),
        ("mimo-v2.5-pro", "¥3/6/0.025"),
        ("kimi-k2.7-code", "¥6.5/27/1.3"),
        ("gemini-3.5-flash", "¥10.875/65.25/1.0875"),
    ],
)
def test_current_model_families_use_cny(model, expected_hint):
    assert price_hint(model) == expected_hint
    assert effective_price("deepseek-v4-flash", "9/10") == {
        "input": 9.0,
        "output": 10.0,
        "currency": "CNY",
    }


def test_estimate_cost_uses_cache_hit_price():
    pricing = lookup_price("deepseek-v4-flash")
    cost = estimate_cost(
        pricing,
        1_000_000,
        500_000,
        cache_hit_tokens=250_000,
        cache_miss_tokens=750_000,
    )
    expected = 0.25 * 0.02 + 0.75 * 1.0 + 0.5 * 2.0
    assert cost == pytest.approx(expected)


def test_db_estimate_cost_uses_saved_custom_price(monkeypatch):
    monkeypatch.setattr(
        "cyrene.model_prices.configured_user_price",
        lambda model: {"input": 7.25, "output": 14.5, "currency": "CNY"},
    )
    assert db._estimate_cost("custom-model", 1_000_000, 1_000_000) == pytest.approx(21.75)


def test_to_usd_preserves_usd_and_converts_cny():
    assert to_usd({"input": 1.0, "output": 2.0, "currency": "USD"}) == {
        "input": 1.0,
        "output": 2.0,
        "currency": "USD",
    }
    converted = to_usd({"input": 7.25, "output": 14.5, "currency": "CNY"})
    assert converted == {"input": 1.0, "output": 2.0, "currency": "USD"}
