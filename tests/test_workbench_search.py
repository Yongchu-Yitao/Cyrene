"""Tests for the Workbench global search endpoint and helpers."""

import asyncio
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cyrene.runtime import database as db
from cyrene.workbench.presentation_runtime import _search_matches, _search_snippet, _search_workbench_items
from route.registry import register_routes


def test_search_matches_substring():
    assert _search_matches("hello", "Hello world") is True
    assert _search_matches("hello world", "Hello   World") is True
    assert _search_matches("foo", "bar") is False
    assert _search_matches("", "text") is False
    assert _search_matches("query", "") is False


def test_search_snippet_centers_match():
    text = "a " * 50 + "needle" + " b " * 50
    snippet = _search_snippet(text, "needle", length=30)
    assert "needle" in snippet
    assert "…" in snippet or len(snippet) <= 30


def test_search_snippet_flexible_whitespace():
    snippet = _search_snippet("hello   world", "hello world")
    assert "hello" in snippet
    assert "world" in snippet


def test_search_snippet_no_match_returns_prefix():
    assert _search_snippet("just some text", "missing").startswith("just")


@pytest.fixture
def temp_db():
    with TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        import asyncio

        asyncio.run(db.init_db(db_path))
        yield db_path


@pytest.mark.asyncio
async def test_search_workbench_items_no_query():
    groups = await _search_workbench_items("", {"project"}, 10)
    assert groups == {"project": []}
