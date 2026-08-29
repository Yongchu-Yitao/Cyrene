"""Configured and built-in token pricing for common language models.

Actual cost tracking follows the model that served the response. An explicit
saved price wins, then the built-in catalog is used, and an unknown unpriced
model records zero. All built-in and unmarked user prices use CNY. A leading
``$`` allows a USD override.

The catalog is intentionally conservative: a model is only matched when its
identifier contains a complete alias, so short or unknown names do not inherit
an unrelated model's price.
"""

from __future__ import annotations

import math
import re

Pricing = dict[str, float | str]

# Official API pricing pages verified on 2026-06-25. USD-only provider prices
# are converted at this fixed rate so display and calculation stay in CNY.
_PRICE_CATALOG_VERIFIED_ON = "2026-06-25"
CNY_PER_USD = 7.25
_CNY_PER_USD = CNY_PER_USD

# Keep only current model generations and place more specific aliases first.
_BUILTIN: list[tuple[tuple[str, ...], Pricing]] = [
    # https://developers.openai.com/api/docs/pricing
    (("gpt-5.5-pro",), {"input": 217.5, "output": 1305.0, "currency": "CNY"}),
    (("gpt-5.5",), {"input": 36.25, "output": 217.5, "cache_hit": 3.625, "currency": "CNY"}),

    # https://platform.claude.com/docs/en/about-claude/pricing
    (("claude-fable-5",), {"input": 72.5, "output": 362.5, "cache_write": 90.625, "cache_hit": 7.25, "currency": "CNY"}),
    (("claude-mythos-5",), {"input": 72.5, "output": 362.5, "cache_write": 90.625, "cache_hit": 7.25, "currency": "CNY"}),

    # https://ai.google.dev/gemini-api/docs/pricing
    (("gemini-3.5-flash",), {"input": 10.875, "output": 65.25, "cache_hit": 1.0875, "currency": "CNY"}),
    (("gemini-3.1-pro-preview",), {"input": 14.5, "output": 87.0, "cache_hit": 1.45, "currency": "CNY"}),

    # https://api-docs.deepseek.com/zh-cn/quick_start/pricing
    (("deepseek-v4-flash",), {"input": 1.0, "output": 2.0, "cache_hit": 0.02, "currency": "CNY"}),
    (("deepseek-v4-pro",), {"input": 3.0, "output": 6.0, "cache_hit": 0.025, "currency": "CNY"}),

    # https://bigmodel.cn/pricing
    (("glm-5.2",), {"input": 8.0, "output": 28.0, "cache_hit": 2.0, "currency": "CNY"}),

    # https://platform.minimaxi.com/docs/guides/pricing-paygo
    # Standard tier, <=512K input, at the permanent 50% rate shown by MiniMax.
    (("minimax-m3",), {"input": 2.1, "output": 8.4, "cache_hit": 0.42, "currency": "CNY"}),

    # https://mimo.mi.com
    (("mimo-v2.5-pro-ultraspeed",), {"input": 9.0, "output": 18.0, "cache_hit": 0.075, "currency": "CNY"}),
    (("mimo-v2.5-pro",), {"input": 3.0, "output": 6.0, "cache_hit": 0.025, "currency": "CNY"}),
    (("mimo-v2.5",), {"input": 1.0, "output": 2.0, "cache_hit": 0.02, "currency": "CNY"}),

    # https://platform.kimi.com/docs/pricing/chat-k27-code
    (("kimi-k2.7-code-highspeed",), {"input": 13.0, "output": 54.0, "cache_hit": 2.6, "currency": "CNY"}),
    (("kimi-k2.7-code",), {"input": 6.5, "output": 27.0, "cache_hit": 1.3, "currency": "CNY"}),
]

_CURRENCY_SYMBOL = {"USD": "$", "CNY": "¥"}


def _normalize_model_id(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    normalized = re.sub(r"[^a-z0-9.-]+", "-", normalized)
    return re.sub(r"-+", "-", normalized).strip("-")


def _contains_alias(model: str, alias: str) -> bool:
    """Match a complete model alias, allowing provider prefixes/date suffixes."""
    return model == alias or model.startswith(alias + "-") or model.endswith("-" + alias) or f"-{alias}-" in model


def lookup_price(model: str) -> Pricing | None:
    """Return a copy of the built-in price for ``model``, if known."""
    model_key = _normalize_model_id(model)
    if not model_key:
        return None
    for aliases, pricing in _BUILTIN:
        if any(_contains_alias(model_key, _normalize_model_id(alias)) for alias in aliases):
            result = dict(pricing)
            result.setdefault("currency", "USD")
            return result
    return None


def _format_number(value: float | str) -> str:
    number = float(value)
    return f"{number:.8f}".rstrip("0").rstrip(".")


def price_hint(model: str) -> str:
    """Return ``input/cache-hit/output`` for use as a price placeholder."""
    pricing = lookup_price(model)
    if not pricing:
        return ""
    symbol = _CURRENCY_SYMBOL.get(str(pricing.get("currency", "USD")), "$")
    input_fmt = _format_number(pricing["input"])
    output_fmt = _format_number(pricing["output"])
    if "cache_hit" in pricing:
        cache_fmt = _format_number(pricing["cache_hit"])
        return f"{symbol}{input_fmt}/{cache_fmt}/{output_fmt}"
    return f"{symbol}{input_fmt}/{output_fmt}"


def parse_user_price(price_str: str, *, default_currency: str = "CNY") -> Pricing | None:
    """Parse ``input[/cache-hit]/output`` with an optional ``$`` or ``¥``.

    Two parts  = ``input/output``
    Three parts = ``input/cache-hit/output``
    """
    value = str(price_str or "").strip()
    if not value:
        return None
    currency = default_currency if default_currency in _CURRENCY_SYMBOL else "USD"
    if value.startswith("¥"):
        currency = "CNY"
        value = value[1:]
    elif value.startswith("$"):
        currency = "USD"
        value = value[1:]
    parts = [part.strip() for part in value.split("/")]
    if len(parts) not in (2, 3):
        return None
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if any(not math.isfinite(number) or number < 0 for number in numbers):
        return None
    if len(numbers) == 3:
        # Auto-detect old format (input/output/cache_hit) where cache_hit was last.
        # In the new format cache_hit is the middle value and should be <= output.
        # Old format: numbers[1] = output > numbers[2] = cache_hit (typically)
        if numbers[1] > numbers[2]:  # cache_hit (mid) > output? means old format
            return {"input": numbers[0], "cache_hit": numbers[2], "output": numbers[1], "currency": currency}
        return {"input": numbers[0], "cache_hit": numbers[1], "output": numbers[2], "currency": currency}
    return {"input": numbers[0], "output": numbers[1], "currency": currency}


def configured_user_price(model: str) -> Pricing | None:
    """Return a valid saved user price for ``model``, if one exists."""
    model_key = _normalize_model_id(model)
    if not model_key:
        return None
    try:
        from cyrene.core.plugin import application_plugin_service

        service = application_plugin_service("model_configuration")
        configured = (
            service.get_model_configuration().get("profiles") or []
            if service is not None
            else []
        )
    except Exception:
        return None
    for item in configured:
        if not isinstance(item, dict):
            continue
        identifiers = (item.get("model"), item.get("name"), item.get("id"))
        if any(_normalize_model_id(str(identifier or "")) == model_key for identifier in identifiers):
            return parse_user_price(str(item.get("price") or ""))
    return None


def effective_price(model: str, user_price_str: str = "") -> Pricing:
    """Resolve explicit/saved pricing, then built-in pricing, else zero.

    The lookup always uses the actual response model. A provider-specific saved
    override therefore remains authoritative while known unpriced models still
    receive the catalog rate.
    """
    built_in = lookup_price(model)
    default_currency = str((built_in or {}).get("currency", "CNY"))
    return (
        parse_user_price(user_price_str, default_currency=default_currency)
        or configured_user_price(model)
        or built_in
        or {"input": 0.0, "output": 0.0, "currency": default_currency}
    )


def to_usd(pricing: Pricing) -> Pricing:
    """Return a copy of ``pricing`` converted to USD."""
    if pricing.get("currency", "USD") == "USD":
        result = dict(pricing)
        result["currency"] = "USD"
        return result
    result: Pricing = {"currency": "USD"}
    for key, value in pricing.items():
        if key == "currency":
            continue
        result[key] = round(float(value) / _CNY_PER_USD, 8)
    return result


def cost_to_cny(cost: float, currency: str) -> float:
    """Convert a monetary amount to the canonical database currency (CNY)."""
    return float(cost) if str(currency or "CNY").upper() == "CNY" else float(cost) * CNY_PER_USD


def cost_from_cny(cost: float, currency: str) -> float:
    """Convert a canonical CNY amount to the requested display currency."""
    if str(currency or "CNY").upper() == "USD":
        return float(cost) / CNY_PER_USD if CNY_PER_USD > 0 else float(cost)
    return float(cost)


def estimate_cost(
    pricing: Pricing,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> float:
    """Calculate cost in the pricing dict's currency."""
    prompt = max(int(prompt_tokens or 0), 0)
    completion = max(int(completion_tokens or 0), 0)
    cache_hit = min(max(int(cache_hit_tokens or 0), 0), prompt)
    cache_miss = max(int(cache_miss_tokens or 0), 0)

    input_price = float(pricing["input"])
    output_price = float(pricing["output"])
    cache_price = float(pricing.get("cache_hit", input_price))
    if cache_hit or cache_miss:
        uncached = max(prompt - cache_hit, cache_miss)
        input_cost = cache_hit * cache_price + uncached * input_price
    else:
        input_cost = prompt * input_price
    return (input_cost + completion * output_price) / 1_000_000
