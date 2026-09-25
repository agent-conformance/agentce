"""AutoGen example (SPEC 13.4 AX-3/AX-4): really imports and runs AutoGen, offline and keyless.

This script builds a real ``autogen_agentchat.agents.AssistantAgent`` with a real, custom-registered
model client and a real tool, and actually ``.run()``s it for one credit-decision scenario. The model
client is ``ScriptedChatCompletionClient``, a subclass of ``autogen_core.models.ChatCompletionClient``
-- AutoGen's own documented, abstract extension point for a custom model backend (every built-in
client, including ``autogen_ext``'s OpenAI/Anthropic/Azure clients, is itself a subclass of this same
ABC). Its ``create()`` is fully scripted and deterministic: no network, no API key. The tool is a real
``autogen_core.tools.FunctionTool`` wrapping ``record_credit_decision``, registered with the agent via
``AssistantAgent(tools=[...])``; AutoGen's own execution loop (``AssistantAgent._execute_tool_call``,
via a ``StaticWorkbench`` autogen builds from the ``tools`` list) invokes it for real through
``FunctionTool.run_json`` -- this script never calls the tool directly. ``reflect_on_tool_use=True``
makes the agent ask the model client a second time, over the real tool result, for a final answer.

Evidence is not hand-assembled after the fact: AutoGen's own structured event/logging system
(``autogen_core.logging``, documented as "to be used by model clients"/"tool subclasses" to log calls)
emits ``LLMCallEvent``/``ToolCallEvent`` instances through the stdlib ``logging`` module under
``autogen_core.EVENT_LOGGER_NAME``. ``ScriptedChatCompletionClient.create()`` logs its own
``LLMCallEvent`` (the same call every real model client in AutoGen makes -- see
``autogen_core.logging.LLMCallEvent``'s own docstring example); ``FunctionTool.run_json`` logs
``ToolCallEvent`` on AutoGen's behalf with zero code from this script. A ``logging.Handler`` attached
to that one named logger turns those real event payloads into ``agentce_emit`` calls, using only the
data the events actually carry (``event.kwargs``).

``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh); the bundle is flushed
at process exit. Neither ``autogen_core`` nor ``autogen_agentchat`` performs any network I/O or
telemetry ping on import or on this scripted run, so no env var is needed to disable one (unlike
CrewAI's install-telemetry ping); this run makes zero network calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import agentce_emit
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import EVENT_LOGGER_NAME, CancellationToken, FunctionCall
from autogen_core.logging import LLMCallEvent, ToolCallEvent
from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    LLMMessage,
    ModelCapabilities,
    ModelFamily,
    ModelInfo,
    RequestUsage,
)
from autogen_core.tools import FunctionTool, Tool, ToolSchema

APPLICANT = "fictional-0001"
SESSION_ID = "autogen-1"
FINAL_ANSWER = "approve"
MODEL_NAME = "scripted-credit-model"


def record_credit_decision(applicant: str) -> dict[str, str]:
    """Record a credit decision for an applicant against the (fictional) credit-core system."""
    return {"status": "recorded"}


class ScriptedChatCompletionClient(ChatCompletionClient):
    """A canned, offline ``autogen_core.models.ChatCompletionClient``: one tool call, then a final
    text response. This is AutoGen's own extension point for a non-network model backend -- see
    ``autogen_core/models/_model_client.py``, where every shipped client (OpenAI, Anthropic, Azure,
    in ``autogen_ext``) is itself a subclass of this same abstract class.
    """

    def __init__(self) -> None:
        self._calls = 0
        self._actual_usage = RequestUsage(prompt_tokens=0, completion_tokens=0)
        self._total_usage = RequestUsage(prompt_tokens=0, completion_tokens=0)
        self._logger = logging.getLogger(EVENT_LOGGER_NAME)

    async def create(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[Tool | ToolSchema] = [],
        tool_choice: Tool | str = "auto",
        json_output: bool | type | None = None,
        # Matches ChatCompletionClient.create's own signature exactly (Mapping[str, Any] = {}) to
        # satisfy the Liskov substitution check mypy runs on this override; never mutated, so the
        # shared default is harmless.
        extra_create_args: Mapping[str, Any] = {},
        cancellation_token: CancellationToken | None = None,
    ) -> CreateResult:
        self._calls += 1
        if self._calls == 1:
            result = CreateResult(
                finish_reason="function_calls",
                content=[
                    FunctionCall(
                        id="call-1",
                        arguments=json.dumps({"applicant": APPLICANT}),
                        name="record_credit_decision",
                    )
                ],
                usage=RequestUsage(prompt_tokens=42, completion_tokens=8),
                cached=False,
            )
        else:
            result = CreateResult(
                finish_reason="stop",
                content=FINAL_ANSWER,
                usage=RequestUsage(prompt_tokens=61, completion_tokens=3),
                cached=False,
            )
        self._actual_usage = RequestUsage(
            prompt_tokens=self._actual_usage.prompt_tokens + result.usage.prompt_tokens,
            completion_tokens=self._actual_usage.completion_tokens
            + result.usage.completion_tokens,
        )
        self._total_usage = self._actual_usage
        # AutoGen's own convention (see autogen_core.logging.LLMCallEvent's docstring): a model
        # client logs its own call to the LLM event logger. Every built-in client does the same;
        # this scripted client is not exempt just because it never leaves the process.
        self._logger.info(
            LLMCallEvent(
                messages=[dict(m.model_dump()) for m in messages],
                response=result.model_dump(mode="json"),
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
            )
        )
        return result

    async def create_stream(self, messages: Sequence[LLMMessage], **kwargs: Any) -> Any:
        # Never exercised: AssistantAgent is constructed with model_client_stream=False (the
        # default), so AutoGen's own execution loop always calls create() instead. Implemented
        # only to satisfy ChatCompletionClient's abstract contract.
        result = await self.create(messages, **kwargs)
        yield result

    async def close(self) -> None:
        return None

    def actual_usage(self) -> RequestUsage:
        return self._actual_usage

    def total_usage(self) -> RequestUsage:
        return self._total_usage

    def count_tokens(
        self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []
    ) -> int:
        return 0

    def remaining_tokens(
        self, messages: Sequence[LLMMessage], *, tools: Sequence[Tool | ToolSchema] = []
    ) -> int:
        return 10_000

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(vision=False, function_calling=True, json_output=False)

    @property
    def model_info(self) -> ModelInfo:
        return ModelInfo(
            vision=False,
            function_calling=True,
            json_output=False,
            family=ModelFamily.UNKNOWN,
            structured_output=False,
        )


class _EvidenceHandler(logging.Handler):
    """Turns AutoGen's own ``EVENT_LOGGER_NAME`` log records into ``agentce_emit`` calls.

    Uses only the data ``LLMCallEvent``/``ToolCallEvent`` payloads (``event.kwargs``) actually carry
    -- the same real-framework-event-payload principle the CrewAI and LangGraph examples apply to
    their own event/callback systems.
    """

    def __init__(self, emitter: agentce_emit.Emitter) -> None:
        super().__init__(level=logging.INFO)
        self._emitter = emitter
        self.last_tool_event: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        event = record.msg
        if isinstance(event, LLMCallEvent):
            data = event.kwargs
            self._emitter.emit_model_call(
                operation="chat",
                provider="scripted",
                model=MODEL_NAME,
                input_tokens=data.get("prompt_tokens"),
                output_tokens=data.get("completion_tokens"),
            )
        elif isinstance(event, ToolCallEvent):
            data = event.kwargs
            self.last_tool_event = self._emitter.emit_tool_call(
                name=data["tool_name"],
                server="mcp://credit-core.internal",
                protocol="mcp",
                args=data["arguments"],
                result=data["result"],
                side_effect="write",
                effect_class="write",
            )


async def _run() -> None:
    emitter = agentce_emit.auto()
    emitter.emit_session_start(environment="production", session_id=SESSION_ID)

    event_logger = logging.getLogger(EVENT_LOGGER_NAME)
    event_logger.setLevel(logging.INFO)
    handler = _EvidenceHandler(emitter)
    event_logger.addHandler(handler)

    tool = FunctionTool(
        record_credit_decision,
        name="record_credit_decision",
        description="Record a credit decision for an applicant against the (fictional) credit-core system.",
    )
    agent = AssistantAgent(
        name="credit_analyst",
        model_client=ScriptedChatCompletionClient(),
        tools=[tool],
        system_message=(
            "You are an experienced credit analyst. Record every decision in the credit-core "
            "system before recommending an outcome."
        ),
        reflect_on_tool_use=True,
    )
    try:
        result = await agent.run(
            task=f"Should we approve applicant {APPLICANT}'s credit application?"
        )
    finally:
        event_logger.removeHandler(handler)

    final_message = result.messages[-1]
    assert isinstance(final_message, TextMessage)
    chosen = final_message.content.strip()

    tool_event = handler.last_tool_event
    emitter.emit_decision(
        decision_type="dom:CreditDecision",
        affects_natural_person=True,
        ai_role="recommendation",
        oversight_modality="review_before",
        chosen=chosen,
        refs={"executed_by": f"agentce:event/{tool_event}"} if tool_event else None,
    )
    emitter.emit_session_end(end_reason="completed", session_id=SESSION_ID)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
