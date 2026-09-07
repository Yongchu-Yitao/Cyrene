from datetime import datetime, timezone
from pathlib import Path

from cyrene.core.context.mounts import stored_context_mounts, unique_context_mounts
from cyrene.core.plugin.plugin import PluginCallResult, PluginFailure
from cyrene.core.plugin.result_codec import decoded_plugin_value, question_options, restored_result, stored_result


def test_result_roundtrip_preserves_failure_time_and_json_fallback():
    failure = PluginFailure('invalid_input', 'try again', retryable=True, retry_scope='different_arguments')
    result = PluginCallResult('call', 'tool', False, {'path': Path('example')}, 'error', datetime(2026, 9, 7, tzinfo=timezone.utc), failure)
    restored = restored_result(stored_result(result))
    assert restored.call_id == result.call_id
    assert restored.time == result.time
    assert restored.failure == failure
    assert restored.success is False
    assert restored.value == {'path': 'example'}
    assert restored.error == 'error'


def test_question_values_remain_bounded_and_keep_original_option_indices():
    assert decoded_plugin_value('not json') == 'not json'
    assert decoded_plugin_value('{broken') == '{broken'
    assert decoded_plugin_value(' {"value": 1} ') == {'value': 1}
    assert question_options(['', {'text': ' second '}, {'id': 'custom', 'label': ' third '}]) == [
        {'id': 'option_2', 'label': 'second'}, {'id': 'custom', 'label': 'third'}]
    assert len(question_options(list('abcdefgh'))) == 6


def test_context_mount_normalization_never_mutates_or_shadows_stable_names():
    mounts = [{'kind': 'a'}, {'kind': 'a.2'}, {'kind': 'a'}, {'kind': 'a'}]
    normalized = unique_context_mounts(mounts)
    assert [m['kind'] for m in normalized] == ['a', 'a.2', 'a.3', 'a.4']
    assert mounts[2] == {'kind': 'a'}
    assert stored_context_mounts([None, {'content': '  '}, {'content': ' value '}]) == [
        {'kind': 'context', 'content': 'value', 'source': 'context_tree', 'lifecycle': ''}]
