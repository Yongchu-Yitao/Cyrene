"""Observe HTTP failures without consuming bodies or changing streaming responses."""
import asyncio

from .repository import record_incident


class DoctorIncidentMiddleware:
    def __init__(self, app, service):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path', '').startswith('/api/') or scope['path'].startswith('/api/doctor/'):
            return await self.app(scope, receive, send)
        started = False

        async def record(code, exc=None):
            error = exc or RuntimeError(code)
            if exc is None:
                error.code = code
            operation = scope.get('method', '') + ' ' + getattr(scope.get('route'), 'path', '<unmatched>')
            return await asyncio.to_thread(record_incident, error, stage='http_request', operation=operation,
                                           directory=self.service.data / 'doctor' / 'incidents')

        async def observed(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                if message['status'] >= 400:
                    identifier = await record('http_' + str(message['status']))
                    message = {**message, 'headers': [*message.get('headers', []), (b'x-cyrene-incident-id', identifier.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, observed)
        except Exception as exc:
            identifier = await record('internal_error', exc)
            if started:
                raise
            from starlette.responses import JSONResponse
            await JSONResponse({'detail': 'Request failed', 'incidentId': identifier}, status_code=500,
                               headers={'x-cyrene-incident-id': identifier})(scope, receive, send)
