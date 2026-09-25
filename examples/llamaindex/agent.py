"""LlamaIndex example (SPEC 13.4 AX-3/AX-4): really imports and runs llama-index-core, offline and keyless.

This script builds a real ``llama_index.core.agent.workflow.FunctionAgent`` -- the current
agent-facing workflow primitive in llama-index-core 0.14.x (confirmed by reading the installed
0.14.25 source: ``FunctionAgent`` is itself a ``Workflow`` subclass, built from ``@step``-decorated
coroutines in ``llama_index/core/agent/workflow/base_agent.py``; the older ``OpenAIAgent``/
``ReActAgent`` "agent runner" classes are a separate, legacy code path this example does not use) --
and actually ``.run()``s it end to end for one credit-decision scenario. The model is
``llama_index.core.llms.mock.MockFunctionCallingLLM``, llama-index-core's own first-party, public
testing LLM (re-exported as ``llama_index.core.llms.MockFunctionCallingLLM``): its documented
``response_generator`` constructor argument is the extension point this script uses to script two
deterministic turns -- one tool call, then a final recommendation -- with no network call and no API
key. The tool is a real ``llama_index.core.tools.FunctionTool`` wrapping ``record_credit_decision``,
invoked by ``FunctionAgent``'s own ``call_tool`` workflow step, never called directly by this script.

Evidence is not hand-assembled after the fact; it is read from two of llama-index-core's own,
independent real-time event mechanisms, each used for what it actually carries:

* **Model calls** come from llama-index-core's instrumentation system
  (``llama_index.core.instrumentation``, itself a thin re-export of the ``llama_index_instrumentation``
  package). ``llm_chat_callback()`` -- the decorator every concrete ``LLM.achat``/``astream_chat``
  implementation carries, including ``MockFunctionCallingLLM``'s -- dispatches a real
  ``LLMChatStartEvent`` then ``LLMChatEndEvent`` (``llama_index/core/instrumentation/events/llm.py``)
  through the module-level ``dispatcher`` in ``llama_index/core/llms/callbacks.py``. A
  ``llama_index_instrumentation.event_handlers.base.BaseEventHandler`` subclass is registered on the
  root dispatcher (``get_dispatcher().add_event_handler(...)``, the pattern llama-index's own
  instrumentation docs use) and turns those two events into one ``emit_model_call`` per turn, using
  only the event's own ``model_dict``/``response`` payload.
* **Tool calls** come from ``FunctionAgent``'s own workflow event stream, not the instrumentation
  dispatcher: reading ``base_agent.py``'s ``call_tool`` step shows it calls the real tool through
  ``FunctionTool.acall`` and then does ``ctx.write_event_to_stream(ToolCallResult(...))`` -- a
  ``llama_index.core.agent.workflow.workflow_events.ToolCallResult`` carrying the tool's own
  ``ToolOutput`` (including ``raw_output``, the literal value ``record_credit_decision`` returned).
  No instrumentation event carries tool-call data for this workflow-based agent API (that only exists
  for the legacy ``AgentRunner`` path via ``AgentToolCallEvent``, unused by ``FunctionAgent``), so this
  script reads ``ToolCallResult`` off the ``WorkflowHandler`` llama-index itself returns from
  ``agent.run()``, via its own documented ``handler.stream_events()`` async generator.

``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh); the bundle is flushed
at process exit. No network call is made anywhere in this module: the LLM is fully scripted and
in-process, and the tool only builds a Python dict.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import agentce_emit
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.agent.workflow.workflow_events import AgentOutput, ToolCallResult
from llama_index.core.base.llms.types import ChatMessage, MessageRole, ToolCallBlock
from llama_index.core.instrumentation import get_dispatcher
from llama_index.core.instrumentation.event_handlers import BaseEventHandler
from llama_index.core.instrumentation.events.base import BaseEvent
from llama_index.core.instrumentation.events.llm import (
    LLMChatEndEvent,
    LLMChatStartEvent,
)
from llama_index.core.llms import MockFunctionCallingLLM
from llama_index.core.tools import FunctionTool
from pydantic import ConfigDict

APPLICANT = "fictional-0001"
SESSION_ID = "llamaindex-1"
FINAL_ANSWER = "approve"
TOOL_NAME = "record_credit_decision"


def record_credit_decision(applicant: str) -> dict[str, str]:
    """Record a credit decision for an applicant against the (fictional) credit-core system."""
    return {"status": "recorded"}


def _scripted_response(messages: list[ChatMessage], **kwargs: Any) -> ChatMessage:
    """The ``response_generator`` ``MockFunctionCallingLLM`` calls on every turn (its own, documented
    constructor extension point): one tool call, then a final recommendation, chosen by looking at
    whether a tool-result message is already in the conversation -- exactly what a real function-
    calling model's decision would depend on.
    """
    if any(message.role == MessageRole.TOOL for message in messages):
        return ChatMessage(role=MessageRole.ASSISTANT, content=FINAL_ANSWER)
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        blocks=[
            ToolCallBlock(
                tool_call_id="call-1",
                tool_name=TOOL_NAME,
                tool_kwargs={"applicant": APPLICANT},
            )
        ],
    )


class _EvidenceHandler(BaseEventHandler):
    """Turns llama-index's own instrumentation events into ``emit_model_call`` calls, using only the
    data those events carry (SPEC 13.4 AX-3: evidence is read from the framework's own mechanism, not
    hand-assembled). Registered on the root dispatcher, it receives every ``LLMChatStartEvent``/
    ``LLMChatEndEvent`` any ``@llm_chat_callback()``-wrapped chat call dispatches, regardless of which
    module fired it.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    emitter: Any
    model_name: str = "scripted-credit-model"

    @classmethod
    def class_name(cls) -> str:
        return "AgentCEEvidenceHandler"

    def handle(self, event: BaseEvent, **kwargs: Any) -> Any:
        if isinstance(event, LLMChatStartEvent):
            # The model's own class name, as the LLM serializes itself for observability
            # (BaseLLM.to_payload) -- not a value this script invented.
            self.model_name = event.model_dict.get("class_name") or self.model_name
        elif isinstance(event, LLMChatEndEvent) and event.response is not None:
            self.emitter.emit_model_call(
                operation="chat", provider="scripted", model=self.model_name
            )


async def _run_agent(emitter: agentce_emit.Emitter) -> tuple[str, str | None]:
    tool = FunctionTool.from_defaults(fn=record_credit_decision)
    llm = MockFunctionCallingLLM(response_generator=_scripted_response)
    agent = FunctionAgent(
        name="credit-analyst",
        description="Decides whether to approve or deny a credit application.",
        system_prompt=(
            "You are an experienced credit analyst who records every decision in the "
            "credit-core system before recommending an outcome."
        ),
        llm=llm,
        tools=[tool],
    )

    handler = agent.run(
        user_msg=f"Should we approve applicant {APPLICANT}'s credit application?"
    )

    last_tool_event: str | None = None
    async for event in handler.stream_events():
        if isinstance(event, ToolCallResult):
            last_tool_event = emitter.emit_tool_call(
                name=event.tool_name,
                server="mcp://credit-core.internal",
                protocol="mcp",
                args=event.tool_kwargs,
                result=event.tool_output.raw_output,
                side_effect="write",
                effect_class="write",
            )

    # FunctionAgent's own `finalize` step (base_agent.py) returns an AgentOutput as the StopEvent
    # result; WorkflowHandler's own await protocol is generic (`RunResultT`), so this cast just names
    # the concrete type llama-index itself produces here.
    result = cast(AgentOutput, await handler)
    return result.response.content or "", last_tool_event


def main() -> None:
    emitter = agentce_emit.auto()
    emitter.emit_session_start(environment="production", session_id=SESSION_ID)

    dispatcher = get_dispatcher()
    dispatcher.add_event_handler(_EvidenceHandler(emitter=emitter))

    chosen, tool_event = asyncio.run(_run_agent(emitter))

    emitter.emit_decision(
        decision_type="dom:CreditDecision",
        affects_natural_person=True,
        ai_role="recommendation",
        oversight_modality="review_before",
        chosen=chosen,
        refs={"executed_by": f"agentce:event/{tool_event}"} if tool_event else None,
    )
    emitter.emit_session_end(end_reason="completed", session_id=SESSION_ID)


if __name__ == "__main__":
    main()
