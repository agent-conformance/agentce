"""Runnable claude-agent-sdk example (SPEC 13.4 AX-3/AX-4): really imports and runs the real
``claude_agent_sdk`` package -- ``ClaudeSDKClient``, its control-protocol ``Query``, and its
NDJSON message parser -- offline and keyless, against a scripted, in-memory ``Transport`` instead
of the real CLI subprocess.

``claude_agent_sdk``'s default transport (``SubprocessCLITransport``) spawns a real ``claude`` CLI
child process that talks to a real, keyed, networked model backend, which cannot run offline. The
SDK's own supported extension point for exactly this situation is ``claude_agent_sdk.Transport``:
an abstract base class, and ``ClaudeSDKClient(transport=...)`` accepts an instance of it in place
of the subprocess transport. ``ScriptedTransport`` below implements it. It replaces *only* the
outbound I/O layer -- the bytes that would otherwise cross stdin/stdout to the ``claude`` binary --
with a canned, deterministic NDJSON feed. Every layer above that is the SDK's own, unmodified code
running against that feed exactly as it would run against the real CLI's: ``ClaudeSDKClient``'s
``connect()``/``query()``/``disconnect()``, ``Query``'s control-protocol handshake (the
"initialize" ``control_request``/``control_response`` exchange it blocks on) and message routing,
and ``claude_agent_sdk._internal.message_parser.parse_message()``.

The scripted session: one assistant turn that calls a ``credit.record_decision`` tool, a
tool-result turn standing in for the CLI's own tool execution (which this script does not run),
a final assistant text turn recommending approval, and a terminal ``result`` message.
``agentce_emit`` reads the model/tool-call data off the SDK's own parsed ``AssistantMessage``/
``ToolUseBlock``/``UserMessage`` objects as they come back through ``receive_response()``, not
from the literal dicts this script wrote into the script.

``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh), so wiring it in
never changes behaviour until it is switched on. The bundle is flushed at process exit.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anyio
import agentce_emit
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Transport,
    UserMessage,
)

APPLICANT = "fictional-0001"
SESSION_ID = "claude-agent-sdk-1"
TOOL_NAME = "credit.record_decision"
TOOL_USE_ID = "toolu-credit-1"
MODEL_NAME = "scripted-claude-agent-sdk"


def _scripted_messages() -> list[dict[str, Any]]:
    """The NDJSON message sequence a real CLI turn would stream for this scenario.

    Shapes and required fields come from reading the installed
    ``claude_agent_sdk._internal.message_parser.parse_message`` and
    ``_internal.transport.subprocess_cli`` source directly, not guessed.
    """
    return [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": MODEL_NAME,
                "id": "msg-credit-1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": TOOL_USE_ID,
                        "name": TOOL_NAME,
                        "input": {"applicant": APPLICANT},
                    }
                ],
                "usage": {"input_tokens": 612, "output_tokens": 24},
                "stop_reason": "tool_use",
            },
            "parent_tool_use_id": None,
            "session_id": SESSION_ID,
            "uuid": "uuid-assistant-1",
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": TOOL_USE_ID,
                        "content": json.dumps({"status": "recorded"}),
                        "is_error": False,
                    }
                ],
            },
            "parent_tool_use_id": None,
            "tool_use_result": {"status": "recorded"},
            "session_id": SESSION_ID,
            "uuid": "uuid-user-1",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": MODEL_NAME,
                "id": "msg-credit-2",
                "content": [{"type": "text", "text": "approve"}],
                "usage": {"input_tokens": 684, "output_tokens": 6},
                "stop_reason": "end_turn",
            },
            "parent_tool_use_id": None,
            "session_id": SESSION_ID,
            "uuid": "uuid-assistant-2",
        },
        {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1200,
            "duration_api_ms": 900,
            "is_error": False,
            "num_turns": 2,
            "session_id": SESSION_ID,
            "result": "approve",
            "usage": {"input_tokens": 1296, "output_tokens": 30},
            "uuid": "uuid-result-1",
        },
    ]


class ScriptedTransport(Transport):
    """Stands in for ``SubprocessCLITransport``: same interface, no subprocess, no network.

    ``write()`` inspects each outgoing frame only to react the way the real CLI would: an
    "initialize" ``control_request`` (the handshake ``Query.initialize()`` blocks on before
    anything else proceeds) gets an immediate ``control_response``, and the first outgoing
    user-turn message triggers the canned assistant/tool/result sequence a real turn would stream
    back. Everything else about how ``Query`` and ``ClaudeSDKClient`` drive a ``Transport`` is
    untouched.
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = script
        self._script_sent = False
        self._ready = False
        self._sender, self._receiver = anyio.create_memory_object_stream[
            dict[str, Any]
        ](max_buffer_size=16)

    async def connect(self) -> None:
        self._ready = True

    async def write(self, data: str) -> None:
        if not self._ready:
            raise RuntimeError("ScriptedTransport is not ready for writing")
        message = json.loads(data)

        if message.get("type") == "control_request":
            if message.get("request", {}).get("subtype") == "initialize":
                await self._sender.send(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": message["request_id"],
                            "response": {},
                        },
                    }
                )
            return

        if message.get("type") == "user" and not self._script_sent:
            self._script_sent = True
            for scripted_message in self._script:
                await self._sender.send(scripted_message)

    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        return self._receiver

    async def close(self) -> None:
        self._ready = False
        await self._sender.aclose()
        await self._receiver.aclose()

    def is_ready(self) -> bool:
        return self._ready

    async def end_input(self) -> None:
        # No real stdin to half-close: ClaudeSDKClient never calls this for a string prompt sent
        # via query() (it only closes input this way for an AsyncIterable prompt stream, which
        # this script does not use), and the scripted feed is fully drained by close() regardless.
        return None


async def _run(emitter: agentce_emit.Emitter) -> None:
    emitter.emit_session_start(environment="production", session_id=SESSION_ID)

    transport = ScriptedTransport(_scripted_messages())
    tool_names: dict[str, str] = {}
    tool_args: dict[str, Any] = {}
    tool_event: str | None = None
    chosen: str | None = None

    async with ClaudeSDKClient(transport=transport) as client:
        await client.query(
            f"Should we approve applicant {APPLICANT}'s credit application?"
        )
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                usage = message.usage or {}
                emitter.emit_model_call(
                    operation="chat",
                    provider="claude-agent-sdk-scripted",
                    model=message.model,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                )
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        tool_names[block.id] = block.name
                        tool_args[block.id] = block.input
                    elif isinstance(block, TextBlock):
                        chosen = block.text
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        result = (
                            message.tool_use_result
                            if message.tool_use_result is not None
                            else block.content
                        )
                        tool_event = emitter.emit_tool_call(
                            name=tool_names.get(block.tool_use_id, block.tool_use_id),
                            server="mcp://credit-core.internal",
                            protocol="mcp",
                            args=tool_args.get(block.tool_use_id),
                            result=result,
                            side_effect="write",
                            effect_class="write",
                        )
            elif isinstance(message, ResultMessage):
                break  # receive_response() also stops here; explicit for clarity

    emitter.emit_decision(
        decision_type="dom:CreditDecision",
        affects_natural_person=True,
        ai_role="recommendation",
        oversight_modality="review_before",
        chosen=chosen or "approve",
        refs={"executed_by": f"agentce:event/{tool_event}"} if tool_event else None,
    )
    emitter.emit_session_end(end_reason="completed", session_id=SESSION_ID)


def main() -> None:
    emitter = agentce_emit.auto()
    anyio.run(_run, emitter)


if __name__ == "__main__":
    main()
