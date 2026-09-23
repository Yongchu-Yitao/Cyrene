"""Explicit graph queries and versioned branch operations."""
from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request

from cyrene.core.context import NodeNotFoundError, TreeNotFoundError
from cyrene.workbench.chat.chat_events import publish_chat_changed
from cyrene.workbench.chat.context_graph_service import ContextGraphService
from cyrene.workbench.chat.context_graph_store import GraphConflict


def register_context_graph_routes(router: APIRouter, context):
    from functools import lru_cache

    @lru_cache(maxsize=1)
    def get_graph():
        return ContextGraphService(context.service, context.conversation_context.agent_states.context_directory, context.db_path)
    locks = defaultdict(asyncio.Lock)

    async def call(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except GraphConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (LookupError, NodeNotFoundError, TreeNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    async def body(request):
        try:
            value = await request.json()
        except ValueError as exc:
            raise HTTPException(400, 'Invalid JSON') from exc
        if not isinstance(value, dict):
            raise HTTPException(400, 'Expected an object')
        return value

    @router.get('/api/workbench/chats/{chat_id}/context-graph')
    async def get_context_graph(chat_id: str):
        return await call(get_graph().graph, chat_id)

    @router.get('/api/workbench/chats/{chat_id}/context-graph/nodes/{node_id}')
    async def get_graph_node(chat_id: str, node_id: str):
        return await call(get_graph().detail, chat_id, node_id)

    @router.post('/api/workbench/chats/{chat_id}/context-graph/branches')
    async def create_graph_branch(chat_id: str, request: Request):
        payload = await body(request)
        # Serialize the journal's prepared/copy/register sequence. An identical
        # request sees the committed result rather than deleting an active copy.
        async with locks[chat_id]:
            result = await call(get_graph().materialize, chat_id, payload)
        chat = result['chat']
        await publish_chat_changed(chat_id, str(chat.get('projectId') or ''), 'forked', fork_chat_id=chat['id'])
        return result

    @router.patch('/api/workbench/chats/{chat_id}/context-graph/view')
    async def save_graph_view(chat_id: str, request: Request):
        payload = await body(request)
        family_id, _, _ = await call(get_graph().family, chat_id)
        value = payload.get('value')
        if not isinstance(value, dict):
            raise HTTPException(400, 'Invalid view')
        return await call(get_graph().store.write, 'view:' + family_id, value, payload.get('expectedRevision'))

    @router.patch('/api/workbench/chats/{chat_id}/context-graph/metadata')
    async def save_graph_metadata(chat_id: str, request: Request):
        payload = await body(request)
        chat = await call(get_graph().chat, chat_id)
        raw = payload.get('value')
        if not isinstance(raw, dict):
            raise HTTPException(400, 'Invalid metadata')
        value = {k: raw[k] for k in ('title', 'color', 'archived', 'favorite', 'notes') if k in raw}
        for key, item in value.items():
            expected_type = bool if key in {'archived', 'favorite'} else str
            if not isinstance(item, expected_type):
                raise HTTPException(400, 'Invalid metadata field: ' + key)
            if isinstance(item, str) and len(item) > (100000 if key == 'notes' else 200):
                raise HTTPException(400, 'Metadata field is too long: ' + key)
        result = await call(get_graph().store.write, 'branch:' + chat_id, value, payload.get('expectedRevision'))
        if value.get('title'):
            await call(context.service.repository.mutate_metadata, chat_id, lambda c: c.update(title=str(value['title'])[:200]))
        await publish_chat_changed(chat_id, str(chat.get('projectId') or ''), 'updated')
        return result
