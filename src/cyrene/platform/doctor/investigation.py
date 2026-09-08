"""Adapter for read-only investigation tools contributed by installed plugins."""
import asyncio
from dataclasses import replace
import json

from cyrene.core.plugin import PluginContext
from .checks import finding
from .evidence import redact, redact_text


class Investigation:
    def __init__(self, host, report, evidence, owner_loop):
        self.host, self.report, self.evidence, self.loop = host, report, evidence, owner_loop
        self.calls = 0
        self.targets = set()
        self.findings = []

    def plugins(self):
        if self.host is None or not getattr(self.host, 'registry', None):
            return ()
        result = []
        for entry in self.host.registry.list_plugins():
            plugin = entry.plugin
            if plugin.metadata.get('doctor_inspection') is not True or plugin.metadata.get('read_only') is not True:
                continue
            if not self.host.registry.plugin_enabled(plugin.name):
                continue
            async def handler(arguments, _context, name=plugin.name):
                return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.call(name, arguments), self.loop))
            result.append(replace(plugin, handler=handler))
        return tuple(result[:8])

    async def call(self, name, arguments):
        self.calls += 1
        if self.calls > 24:
            raise ValueError('Investigation budget exhausted')
        context = PluginContext(data={'session_id': self.report['scope'].get('chat_id', ''), 'language': self.report['language']},
            services={'plugin_maintenance_host': self.host})
        result = await self.host.runtime.call(name, arguments, context)
        if not result.success:
            return {'error': redact(result.error)}
        value = result.value
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = {'result': value}
        if not isinstance(value, dict):
            return {'error': 'Investigation requires an object result'}
        target = value.get('target')
        is_read = arguments.get('action') == 'read' and target in self.report.get('plugin_targets', []) and value.get('sha256') and value.get('path')
        if is_read:
            self.targets.add(target)
        item = finding('repair_source_inspected' if is_read else 'repair_investigation', 'info',
            '已调查相关功能', 'Related functionality investigated',
            {'provider': name, 'operation': arguments.get('action'), 'target': target if is_read else None,
             'path': value.get('path'), 'sha256': value.get('sha256')})
        item['id'] = 'i' + str(len(self.findings) + 1)
        self.findings.append(item)
        self.evidence[item['id']] = item
        public = redact(value)
        if isinstance(value.get('source_text'), str):
            public['source_text'] = redact_text(value['source_text'], limit=64000)
        return {'evidence_id': item['id'], **public}
