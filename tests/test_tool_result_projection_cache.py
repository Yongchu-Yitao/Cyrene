import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from cyrene.plugins.builtin.cyrene_content import tool_result_store as store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, '_RESULT_ROOT', tmp_path / 'results')
    with store._CACHE_LOCK:
        store._METRICS_CACHE.clear()
        store._PREVIEW_CACHE.clear()
        store._preview_cache_bytes = 0
    yield
    with store._CACHE_LOCK:
        store._METRICS_CACHE.clear()
        store._PREVIEW_CACHE.clear()
        store._preview_cache_bytes = 0


def project(text='证据' * 10000, budget=1000, session='a'):
    return store.project_tool_result_for_model(
        text, tool_name='Fetch', tool_call_id='call', session_id=session,
        token_limit_tokens=budget,
    )


def test_replay_reuses_counts_and_preview_but_budget_and_content_changes_recompute(monkeypatch):
    original_count, original_preview = store._token_count, store._preview
    counts, previews = [], []
    def count(text):
        counts.append(text)
        return original_count(text)
    def preview(text, **kwargs):
        previews.append(kwargs['token_limit'])
        return original_preview(text, **kwargs)
    monkeypatch.setattr(store, '_token_count', count)
    monkeypatch.setattr(store, '_preview', preview)
    raw = '证据' * 10000
    first = project(raw)
    count_after_first = len(counts)
    assert project(raw) == first
    assert len(counts) == count_after_first
    assert previews == [1000]
    changed_budget = project(raw, budget=500)
    assert original_count(changed_budget.content) <= 500
    assert previews == [1000, 500]
    assert counts.count(raw) == 1
    assert project(raw + 'changed').content_ref != first.content_ref
    assert project(raw, session='b').content_ref != first.content_ref


def test_batch_measures_each_original_once(monkeypatch):
    raw = '批量' * 10000
    original_count = store._token_count
    calls = []
    def count(text):
        calls.append(text)
        return original_count(text)
    monkeypatch.setattr(store, '_token_count', count)
    results = [(raw, 'Fetch', 'a'), (raw, 'Fetch', 'b')]
    first = store.project_tool_result_batch_for_model(results, session_id='a', context_limit_tokens=100000)
    assert calls.count(raw) == 1
    count_after_first = len(calls)
    assert store.project_tool_result_batch_for_model(results, session_id='a', context_limit_tokens=100000) == first
    assert len(calls) == count_after_first


@pytest.mark.parametrize('expired', [False, True])
def test_cached_reference_is_restored_without_regenerating_preview(monkeypatch, expired):
    first = project()
    path = store._resolve_reference(first.content_ref, session_id='a')
    if expired:
        old = time.time() - store._RESULT_TTL_SECONDS - 1
        os.utime(path, (old, old))
    else:
        path.unlink()
    def unexpected(*args, **kwargs):
        pytest.fail('unchanged content and budget must reuse cached computation')
    monkeypatch.setattr(store, '_preview', unexpected)
    monkeypatch.setattr(store, '_token_count', unexpected)
    assert project() == first
    assert store._resolve_reference(first.content_ref, session_id='a').read_text() == '证据' * 10000


def test_storage_failure_is_not_cached_and_recovery_retries(monkeypatch):
    save = store.store_tool_result
    def fail(*args, **kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr(store, 'store_tool_result', fail)
    assert project().content_ref is None
    assert not store._PREVIEW_CACHE
    monkeypatch.setattr(store, 'store_tool_result', save)
    first = project()
    store._resolve_reference(first.content_ref, session_id='a').unlink()
    monkeypatch.setattr(store, 'store_tool_result', fail)
    assert project().content_ref is None  # Never return a stale cached reference.
    monkeypatch.setattr(store, 'store_tool_result', save)
    assert project() == first


def test_caches_are_bounded_under_concurrent_use(monkeypatch):
    monkeypatch.setattr(store, '_METRICS_CACHE_ENTRIES', 4)
    monkeypatch.setattr(store, '_PREVIEW_CACHE_ENTRIES', 3)
    monkeypatch.setattr(store, '_PREVIEW_CACHE_BYTES', 16000)
    def run(i):
        return project(('证据' * 2000) + str(i), session=str(i % 2))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(12)))
    assert all(result.content_ref for result in results)
    assert len(store._METRICS_CACHE) <= 4
    assert len(store._PREVIEW_CACHE) <= 3
    assert store._preview_cache_bytes <= 16000
    assert store._preview_cache_bytes == sum(len(v.content) * 4 + 1024 for v in store._PREVIEW_CACHE.values())
