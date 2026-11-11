"""File postconditions run in the original workspace and never infer shell effects."""

import importlib
from pathlib import Path
import shlex
import sys

import pytest

from cyrene.core.plugin import PluginContext, PluginExecutionError
from cyrene.core.plugin.core_impl.bash_checks import inspect_file_checks, prepare_file_checks
from cyrene.core.plugin.core_impl.permission_boundaries import bash_boundary

bash_module = importlib.import_module('cyrene.core.plugin.core_impl.bash')


def python_command(script):
    return f'{shlex.quote(sys.executable)} -c {shlex.quote(script)}'


@pytest.mark.asyncio
async def test_move_and_verify_in_one_call_despite_stderr(tmp_path):
    source = tmp_path / 'source [1].txt'
    source.write_text('keep')
    result = await bash_module.bash({
        'command': python_command("from pathlib import Path; import sys; Path('source [1].txt').rename('target [1].txt'); print('warning', file=sys.stderr)"),
        'file_checks': [{'path': source.name, 'expected': 'absent'},
                        {'path': 'target [1].txt', 'expected': 'file'}],
    }, PluginContext(workspace=tmp_path))
    assert result['command_status'] == 'completed'
    assert result['verification_status'] == 'passed' and result['stderr'].strip() == 'warning'
    assert (tmp_path / 'target [1].txt').read_text() == 'keep'
    assert [r['actual'] for r in result['file_checks']] == ['absent', 'file']


@pytest.mark.asyncio
@pytest.mark.parametrize('exit_code,expected,verification', [(0, 'absent', 'failed'), (7, 'file', 'passed')])
async def test_command_status_does_not_override_verified_effect(tmp_path, exit_code, expected, verification):
    result = await bash_module.bash({
        'command': python_command(f"from pathlib import Path; import sys; Path('created').write_text('once'); sys.exit({exit_code})"),
        'file_checks': [{'path': 'created', 'expected': expected}],
    }, PluginContext(workspace=tmp_path))
    assert result['command_status'] == ('completed' if exit_code == 0 else 'failed')
    assert result['verification_status'] == verification
    assert result['recovery_action'] == 'inspect_state_before_retry'
    assert (tmp_path / 'created').read_text() == 'once'


@pytest.mark.asyncio
async def test_checks_use_initial_workspace_and_keep_symlinks(tmp_path):
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'link').symlink_to(tmp_path / 'missing')
    result = await bash_module.bash({
        'command': 'cd sub',
        'file_checks': [{'path': 'sub', 'expected': 'directory'}, {'path': 'link', 'expected': 'symlink'},
                        {'path': 'link', 'expected': 'exists'}],
    }, PluginContext(workspace=tmp_path))
    assert result['verification_status'] == 'passed'


@pytest.mark.asyncio
async def test_invalid_checks_are_rejected_before_mutation(tmp_path):
    with pytest.raises(ValueError):
        await bash_module.bash({
            'command': python_command("from pathlib import Path; Path('wrong').touch()"),
            'file_checks': [{'path': 'wrong', 'expected': 'invalid'}],
        }, PluginContext(workspace=tmp_path))
    assert not (tmp_path / 'wrong').exists()


def test_stat_errors_are_not_reported_as_absence(tmp_path, monkeypatch):
    checks = prepare_file_checks({'file_checks': [{'path': 'private', 'expected': 'absent'}]},
                                 PluginContext(workspace=tmp_path))
    def denied(_):
        raise PermissionError(13, 'denied')
    monkeypatch.setattr(Path, 'lstat', denied)
    result = inspect_file_checks(checks)
    assert result['verification_status'] == 'failed'
    assert result['file_checks'][0]['status'] == 'error'
    assert 'actual' not in result['file_checks'][0]


def test_file_check_permission_scope_and_shell_review_are_preserved(tmp_path):
    context = PluginContext(workspace=tmp_path)
    inside = {'path': 'output', 'expected': 'file'}
    outside = {'path': str(tmp_path.parent / 'external'), 'expected': 'absent'}
    assert bash_boundary({'command': 'echo ok', 'file_checks': [inside]}, context) is None
    request = bash_boundary({'command': 'echo ok', 'file_checks': [outside]}, context)
    assert request['kind'] == 'read_elevation' and request['path_hint'] == outside['path']
    request = bash_boundary({'command': 'rm output', 'file_checks': [outside]}, context)
    assert request['kind'] == 'destructive_confirmation' and request['requires_human']
    assert request['verification_paths'] == [outside['path']]


def test_file_checks_review_symlink_parent_escape(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (workspace / 'escape').symlink_to(outside, target_is_directory=True)
    context = PluginContext(workspace=workspace)
    request = bash_boundary({'command': 'echo ok', 'file_checks': [
        {'path': 'escape/file', 'expected': 'exists'}]}, context)
    assert request['kind'] == 'read_elevation'
    # A link outside the workspace still needs review even if its target is inside.
    (outside / 'link').symlink_to(workspace / 'missing')
    request = bash_boundary({'command': 'echo ok', 'file_checks': [
        {'path': str(outside / 'link'), 'expected': 'symlink'}]}, context)
    assert request['kind'] == 'read_elevation'


@pytest.mark.asyncio
async def test_timeout_reports_postconditions_without_repeating_command(tmp_path):
    with pytest.raises(PluginExecutionError) as caught:
        await bash_module.bash({
            'command': python_command("from pathlib import Path; import time; Path('started').touch(); time.sleep(10)"),
            'timeout_ms': 500,
            'file_checks': [{'path': 'started', 'expected': 'file'}],
        }, PluginContext(workspace=tmp_path))
    details = caught.value.failure.details
    assert details['command_status'] == 'timed_out'
    assert details['verification_status'] == 'passed'
    assert details['recovery_action'] == 'inspect_state_before_retry'
