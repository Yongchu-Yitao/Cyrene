"""Collect weather evidence for the home card using Workbench's configured search."""
import asyncio
import logging

from cyrene.core.plugin.scopes import application_plugin_service

logger = logging.getLogger(__name__)


def _overview_location(cards):
    for card in cards:
        if card["spec"].get("home_kind") != "overview":
            continue
        for block in card["spec"].get("blocks", []):
            if block.get("type") in {"weather", "weather_observation"} and block.get("location"):
                return block["location"].strip()
            if block.get("type") == "text":
                place, separator, status = block.get("text", "").partition(" · ")
                if separator and (block.get("block_id") == "home_weather_status" or status == "天气暂缺"):
                    return place.strip()
    return ""


async def weather_snapshot(cards):
    location = _overview_location(cards)
    search = application_plugin_service("web_search")
    snapshot = {"location": location, "status": "unavailable"}
    if not location or search is None:
        return snapshot
    try:
        async with asyncio.timeout(90):
            evidence = await search.search(
                f"{location} current local weather temperature latest observation time and source",
                detail="preview", max_results=2,
            )
    except Exception:
        logger.warning("Home weather search unavailable", exc_info=True)
        return snapshot
    if evidence.strip():
        snapshot.update(status="search_results", evidence=evidence[:6000])
    return snapshot
