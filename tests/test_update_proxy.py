import hashlib

import httpx
import pytest

from cyrene.platform import config_store, settings_service, updater


def test_update_proxy_scope_is_a_boolean_general_setting():
    changes, specs = settings_service.validate_changes(
        'runtime', {'proxy_updates_enabled': True}, actor='ui',
    )
    assert changes == {'proxy_updates_enabled': True}
    assert specs['proxy_updates_enabled'].tab == 'general'


@pytest.mark.parametrize(('master', 'enabled'), [(False, False), (False, True), (True, False), (True, True)])
async def test_checks_and_resumed_downloads_share_explicit_proxy(monkeypatch, tmp_path, master, enabled):
    proxy = 'http://127.0.0.1:7897' if master and enabled else ''
    options = []
    requests = []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/latest'):
            return httpx.Response(200, json={'tag_name': 'v1.0.0'})
        assert request.headers['Range'] == 'bytes=3-'
        return httpx.Response(206, content=b'def', headers={'Content-Range': 'bytes 3-5/6'})

    def client(**kwargs):
        options.append(kwargs.copy())
        kwargs.pop('proxy')  # Mock transport exercises the HTTP paths without real network access.
        return real_client(**kwargs, transport=httpx.MockTransport(respond))

    values = {
        'external_agent_proxy_enabled': master,
        'external_agent_proxy_url': 'http://127.0.0.1:7897',
        'proxy_updates_enabled': enabled,
    }
    monkeypatch.setattr(config_store, 'get_setting', lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(updater.httpx, 'AsyncClient', client)
    monkeypatch.setattr(updater, '_current_version', lambda: '1.0.0')
    monkeypatch.setenv('HTTPS_PROXY', 'http://unwanted.example:9999')
    info = await updater.check_for_update(include_prerelease=False)
    assert info.latest_version == '1.0.0'
    package = tmp_path / 'update.exe'
    package.write_bytes(b'abc')
    result = await updater._download_to(package, 'https://example.test/update.exe', None)
    assert result.sha256 == hashlib.sha256(b'abcdef').hexdigest()
    assert result.size == 6
    assert len(requests) == len(options) == 2
    assert [option['timeout'] for option in options] == [15.0, 600.0]
    for option in options:
        assert option['proxy'] == (proxy or None)
        assert option['trust_env'] is False
        assert option['follow_redirects'] is True


async def test_unreachable_proxy_surfaces_check_error(monkeypatch):
    def unavailable(**kwargs):
        raise httpx.ConnectError('Proxy unavailable')
    monkeypatch.setattr(updater, '_update_http_client', unavailable)
    info = await updater.check_for_update(include_prerelease=False)
    assert not info.available
    assert info.error
