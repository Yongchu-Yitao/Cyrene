"""Accumulate provider output independently of transport timeout/cancellation."""


class CodexTurnOutput:
    def __init__(self, stream_callback, action_schema, action_tools, emit_transport):
        self.stream_callback = stream_callback
        self.action_schema = action_schema
        self.action_tools = action_tools
        self.emit_transport = emit_transport
        self.text_parts = []
        self.reasoning_parts = []
        self.final_text = ""
        self.usage = {}
        self.reasoning_started = False
        self.upstream_signal_seen = False

    async def text_delta(self, params):
        delta = str(params.get("delta") or "")
        if delta:
            if not self.upstream_signal_seen:
                self.upstream_signal_seen = True
                await self.emit_transport("connected")
            self.text_parts.append(delta)
            if self.stream_callback and self.action_schema is None:
                await self.stream_callback(
                    {"type": "reply_delta", "delta": delta}
                )

    async def reasoning_delta(self, params):
        delta = str(params.get("delta") or "")
        if delta:
            if not self.upstream_signal_seen:
                self.upstream_signal_seen = True
                await self.emit_transport("connected")
            if self.stream_callback and not self.reasoning_started:
                await self.stream_callback({"type": "reasoning_start"})
            self.reasoning_started = True
            self.reasoning_parts.append(delta)
            if self.stream_callback:
                await self.stream_callback(
                    {"type": "reasoning_delta", "delta": delta}
                )

    async def finish(self, normalize_action):
        content = self.final_text or "".join(self.text_parts)
        reasoning_content = "".join(self.reasoning_parts)
        response = {
            "role": "assistant",
            "content": content,
            "usage": self.usage,
        }
        if self.action_schema is not None:
            response = normalize_action(
                content,
                self.action_tools,
                usage=self.usage,
            )
        if self.reasoning_started and self.stream_callback:
            await self.stream_callback(
                {
                    "type": "reasoning_done",
                    "response": reasoning_content,
                }
            )
        if self.stream_callback and self.action_schema is None:
            await self.stream_callback({"type": "reply_done", "response": content})
        elif (
            self.stream_callback
            and not response.get("tool_calls")
            and str(response.get("content") or "")
        ):
            visible_content = str(response["content"])
            await self.stream_callback({"type": "reply_start"})
            await self.stream_callback(
                {"type": "reply_delta", "delta": visible_content}
            )
            await self.stream_callback(
                {"type": "reply_done", "response": visible_content}
            )
        if reasoning_content:
            response["reasoning_content"] = reasoning_content
        return response
