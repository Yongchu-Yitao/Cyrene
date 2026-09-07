"""Per-stream protocol decoding; transport and final validation remain separate."""
from __future__ import annotations

import uuid


class StreamDecoder:
    def __init__(self, adapter, callback, diagnostics, *, usage_for, parse_response,
                 update_diagnostics, stream_error, openai_event, openai_adapters):
        self.adapter = adapter
        self.callback = callback
        self.diagnostics = diagnostics
        self.usage_for = usage_for
        self.parse_response = parse_response
        self.update_diagnostics = update_diagnostics
        self.stream_error = stream_error
        self.openai_event = openai_event
        self.text_parts = []
        self.reasoning_parts = []
        self.tool_calls = {}
        self.usage = {}
        self.finish = ""
        self.started = False
        self.reasoning_started = False
        self.consume = {
            "anthropic": self.anthropic,
            "openai_responses": self.responses,
            "gemini": self.gemini,
        }.get(adapter, self.openai if adapter in openai_adapters else self.ignore)

    async def ignore(self, data):
        pass

    async def emit_text(self, text):
        if not text:
            return
        if not self.started and self.callback:
            await self.callback({"type": "reply_start"})
            self.started = True
        self.text_parts.append(text)
        if self.callback:
            await self.callback({"type": "reply_delta", "delta": text})

    async def emit_reasoning(self, reasoning):
        if reasoning:
            if self.callback and not self.reasoning_started:
                await self.callback({"type": "reasoning_start"})
                self.reasoning_started = True
            self.reasoning_parts.append(reasoning)
            if self.callback:
                await self.callback({"type": "reasoning_delta", "delta": reasoning})

    async def anthropic(self, data):
        event_type = str(data.get("type") or "")
        if event_type == "message_start":
            self.usage.update(self.usage_for(self.adapter, data.get("message") or {}))
            return
        if event_type == "content_block_start":
            await self.anthropic_block_start(data)
            return
        if event_type == "content_block_delta":
            delta = data.get("delta") if isinstance(data.get("delta"), dict) else {}
            if delta.get("type") == "text_delta":
                await self.emit_text(str(delta.get("text") or ""))
                return
            if delta.get("type") == "input_json_delta":
                key = str(data.get("index") or 0)
                if key not in self.tool_calls:
                    self.tool_calls[key] = {"id": f"toolu_{uuid.uuid4().hex}", "name": "", "arguments": ""}
                self.tool_calls[key]["arguments"] += str(delta.get("partial_json") or "")
                return
            if delta.get("type") == "thinking_delta":
                reasoning = str(delta.get("thinking") or "")
                await self.emit_reasoning(reasoning)
            return
        if event_type == "message_delta":
            self.anthropic_message_delta(data)
            return
        if event_type == "message_stop":
            self.diagnostics["terminal_event_seen"] = True

    async def responses(self, data):
        event_type = str(data.get("type") or "")
        if event_type == "response.output_text.delta":
            await self.emit_text(str(data.get("delta") or ""))
            return
        if event_type == "response.reasoning_summary_text.delta":
            reasoning = str(data.get("delta") or "")
            await self.emit_reasoning(reasoning)
            return
        if event_type == "response.output_item.added":
            self.responses_item_added(data)
            return
        if event_type == "response.function_call_arguments.delta":
            key = str(data.get("output_index") or 0)
            # Existing calls do not need a discarded UUID and default record
            # for every argument fragment.
            if key not in self.tool_calls:
                self.tool_calls[key] = {"id": str(data.get("item_id") or f"call_{uuid.uuid4().hex}"), "name": str(data.get("name") or ""), "arguments": ""}
            self.tool_calls[key]["arguments"] += str(data.get("delta") or "")
            return
        if event_type == "response.function_call_arguments.done":
            self.responses_arguments_done(data)
            return
        if event_type == "response.output_item.done":
            self.responses_item_done(data)
            return
        if event_type == "response.failed":
            self.responses_failed(data)
            return
        if event_type in {"response.completed", "response.incomplete"}:
            self.responses_completed(data, event_type)

    async def gemini(self, data):
        parsed = self.parse_response(self.adapter, data)
        await self.emit_text(str(parsed.get("content") or ""))
        reasoning = str(parsed.get("reasoning_content") or "")
        await self.emit_reasoning(reasoning)
        for call_index, call in enumerate(parsed.get("tool_calls") or []):
            function = call.get("function") or {}
            key = f"gemini:{call_index}:{str(function.get('name') or '')}"
            self.tool_calls[key] = {
                "id": str(call.get("id") or ""),
                "name": str(function.get("name") or ""),
                "arguments": str(function.get("arguments") or "{}"),
            }
        self.usage.update(parsed.get("usage") or {})
        self.finish = str(parsed.get("finish_reason") or self.finish)

    async def openai(self, data):
        event_finish, self.reasoning_started = await self.openai_event(
            self.adapter, data, self.emit_text, self.callback, self.reasoning_parts,
            self.tool_calls, self.usage, self.reasoning_started,
        )
        self.finish = event_finish or self.finish

    async def anthropic_block_start(self, data):
        block = data.get("content_block") if isinstance(data.get("content_block"), dict) else {}
        if block.get("type") == "tool_use":
            call_id = str(block.get("id") or f"toolu_{uuid.uuid4().hex}")
            self.tool_calls[str(data.get("index") or 0)] = {"id": call_id, "name": str(block.get("name") or ""), "arguments": ""}
        elif block.get("type") == "text":
            await self.emit_text(str(block.get("text") or ""))

    def anthropic_message_delta(self, data):
        self.finish = str((data.get("delta") or {}).get("stop_reason") or self.finish)
        raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        if raw_usage:
            next_usage = self.usage_for(self.adapter, {"usage": raw_usage})
            if any(
                key in raw_usage
                for key in (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            ):
                self.usage["prompt_tokens"] = next_usage["prompt_tokens"]
                for key in (
                    "prompt_cache_hit_tokens",
                    "prompt_cache_miss_tokens",
                ):
                    if key in next_usage:
                        self.usage[key] = next_usage[key]
            if "output_tokens" in raw_usage:
                self.usage["completion_tokens"] = next_usage["completion_tokens"]
            self.usage["total_tokens"] = (
                int(self.usage.get("prompt_tokens") or 0)
                + int(self.usage.get("completion_tokens") or 0)
            )

    def responses_item_added(self, data):
        item = data.get("item") if isinstance(data.get("item"), dict) else {}
        if item.get("type") == "function_call":
            key = str(data.get("output_index") or len(self.tool_calls))
            self.tool_calls[key] = {"id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"), "name": str(item.get("name") or ""), "arguments": str(item.get("arguments") or "")}

    def responses_arguments_done(self, data):
        key = str(data.get("output_index") or 0)
        call = self.tool_calls.setdefault(key, {
            "id": str(data.get("item_id") or f"call_{uuid.uuid4().hex}"),
            "name": str(data.get("name") or ""),
            "arguments": "",
        })
        call["arguments"] = str(data.get("arguments") or "{}")

    def responses_item_done(self, data):
        item = data.get("item") if isinstance(data.get("item"), dict) else {}
        if item.get("type") == "function_call":
            key = str(data.get("output_index") or 0)
            self.tool_calls[key] = {
                "id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"),
                "name": str(item.get("name") or ""),
                "arguments": str(item.get("arguments") or "{}"),
            }

    def responses_failed(self, data):
        failed = data.get("response")
        failed = failed if isinstance(failed, dict) else {}
        error = failed.get("error")
        error = error if isinstance(error, dict) else {}
        # Retain only known codes, never upstream messages or request data.
        code = str(error.get("code") or "")
        known_codes = {
            "server_error", "rate_limit_exceeded", "insufficient_quota",
            "invalid_api_key", "authentication_error", "context_length_exceeded",
            "invalid_prompt", "invalid_request_error", "model_not_found",
        }
        self.diagnostics["provider_error_code"] = code if code in known_codes else "unknown"
        self.diagnostics["terminal_event_seen"] = True
        self.diagnostics["finish_reason"] = "failed"
        self.diagnostics["termination_reason"] = "provider_response_failed"
        self.update_diagnostics(self.diagnostics, self.tool_calls)
        raise self.stream_error(
            "provider_failed", "The provider reported a failed response.", self.diagnostics,
        )

    def responses_completed(self, data, event_type):
        self.diagnostics["terminal_event_seen"] = True
        completed = data.get("response") if isinstance(data.get("response"), dict) else {}
        self.usage.update(self.usage_for(self.adapter, completed))
        self.finish = str(completed.get("status") or self.finish)
        if event_type == "response.incomplete":
            self.responses_incomplete(completed)
        completed_message = self.parse_response(self.adapter, completed)
        for call in completed_message.get("tool_calls") or []:
            self.merge_completed_call(call)

    def responses_incomplete(self, completed):
        details = completed.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        if reason == "max_output_tokens":
            self.finish = "length"
        else:
            self.diagnostics["finish_reason"] = "incomplete"
            self.diagnostics["termination_reason"] = "provider_response_incomplete"
            self.update_diagnostics(self.diagnostics, self.tool_calls)
            raise self.stream_error(
                "upstream_incomplete",
                "The provider reported an incomplete response.",
                self.diagnostics,
            )

    def merge_completed_call(self, call):
        call_id = str(call.get("id") or "")
        existing = next(
            (
                value
                for value in self.tool_calls.values()
                if value.get("id") == call_id
            ),
            None,
        )
        if existing is not None:
            existing.update({
                "name": str((call.get("function") or {}).get("name") or existing.get("name") or ""),
                "arguments": str((call.get("function") or {}).get("arguments") or "{}"),
            })
        else:
            self.tool_calls[str(len(self.tool_calls))] = {
                "id": call_id,
                "name": str((call.get("function") or {}).get("name") or ""),
                "arguments": str((call.get("function") or {}).get("arguments") or "{}"),
            }
