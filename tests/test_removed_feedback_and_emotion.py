"""Regression coverage for retired inference features, including legacy stores."""
import json
import sqlite3

import pytest

from cyrene.plugins.builtin.cyrene_memory import short_term


def test_compression_stores_facts_without_emotion_scores(monkeypatch, tmp_path):
    monkeypatch.setattr(short_term, '_SHORT_TERM_FILE', tmp_path / 'short_term.json')
    monkeypatch.setattr(short_term, '_STATS_DB_PATH', '')
    short_term._store_compressed_memories(
        '[fact] user loves a happy keyboard\n[emotion] user feels stressed'
    )
    entries = short_term.load_entries()
    assert len(entries) == 1
    assert entries[0]['type'] == 'fact'
    assert 'emotional_valence' not in entries[0]


def test_old_score_neither_leaks_nor_preserves_expired_memory(monkeypatch, tmp_path):
    path = tmp_path / 'short_term.json'
    monkeypatch.setattr(short_term, '_SHORT_TERM_FILE', path)
    path.write_text(json.dumps([
        {'content': 'old conversation', 'type': 'pattern', 'last_mentioned': '2000-01-01',
         'emotional_valence': 5, 'source': 'legacy'},
        {'content': 'user preference', 'type': 'preference', 'last_mentioned': '2000-01-01'},
    ]))
    assert 'emotional_valence' not in short_term.load_entries()[0]
    assert short_term.load_entries()[0]['source'] == 'legacy'
    short_term.clear_old_entries()
    assert [e['content'] for e in short_term.load_entries()] == ['user preference']


@pytest.mark.asyncio
async def test_memory_statistics_have_no_emotion_dependency(tmp_path):
    from cyrene.platform import database
    path = str(tmp_path / 'stats.db')
    await database.init_db(path)
    database.record_memory_touch_sync(path, day='2026-11-11', is_new=True)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT memory_new, memory_mentions FROM daily_stats WHERE day='2026-11-11'").fetchone() == (1, 1)
        assert not {'emotion_sum', 'emotion_count'} & {r[1] for r in conn.execute('PRAGMA table_info(daily_stats)')}


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['做得不错', '不对，重新做', 'fix this instead'])
async def test_new_turn_does_not_mark_previous_turn_as_feedback(monkeypatch, tmp_path, message):
    from cyrene.plugins.builtin.cyrene_skills import orchestrator as learning
    for key in ('_DATA_DIR', '_WORKSPACE_DIR', '_DB_FILE', '_INIT_DONE'):
        monkeypatch.setattr(learning, key, getattr(learning, key))
    await learning.init(tmp_path, tmp_path)
    first = await learning.begin_turn(session_id='s', round_id='one', user_message='start', history=[])
    learning.clear_turn_context(first)
    with sqlite3.connect(tmp_path / 'behavior-learning.db') as conn:
        before = conn.execute('SELECT * FROM behavior_turns WHERE turn_id=?', (first['turn_id'],)).fetchone()
    second = await learning.begin_turn(session_id='s', round_id='two', user_message=message, history=[])
    learning.clear_turn_context(second)
    with sqlite3.connect(tmp_path / 'behavior-learning.db') as conn:
        assert before == conn.execute('SELECT * FROM behavior_turns WHERE turn_id=?', (first['turn_id'],)).fetchone()
        assert 'user_feedback' not in {r[1] for r in conn.execute('PRAGMA table_info(behavior_turns)')}
        assert all('correction_feedback' not in json.loads(r[0]) for r in conn.execute('SELECT metadata_json FROM behavior_turns'))
