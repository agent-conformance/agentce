"""LangGraph example (SPEC 13.4 AX-3/AX-4): really imports and runs LangGraph, offline and keyless.

This script builds a real ``langgraph.graph.StateGraph`` with a model node and a tool node, and invokes
it end to end for one credit-decision scenario. The model node is backed by
``langchain_core.language_models.fake_chat_models.FakeMessagesListChatModel``: a scripted, deterministic
chat model that returns canned messages with no network call and no API key. The tool node runs a real
Python tool (``record_credit_decision``) through LangGraph's own ``ToolNode``.

Evidence is not hand-assembled after the fact: a ``BaseCallbackHandler`` is passed to ``graph.invoke``
and LangGraph/LangChain's own callback events (``on_chat_model_start``/``on_llm_end``,
``on_tool_start``/``on_tool_end``) drive the ``agentce_emit`` calls, using only the data those events
carry. ``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh); the bundle is
flushed at process exit.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import agentce_emit
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

APPLICANT = "fictional-0001"
SESSION_ID = "langgraph-1"


@tool
def record_credit_decision(applicant: str) -> dict[str, str]:
    """Record a credit decision for an applicant against the (fictional) credit-core system."""
    return {"status": "recorded"}


def _scripted_model() -> FakeMessagesListChatModel:
    """A canned, offline chat model: one tool call, then a final recommendation, no network."""
    return FakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "record_credit_decision",
                        "args": {"applicant": APPLICANT},
                        "id": "call-1",
                    }
                ],
                usage_metadata={
                    "input_tokens": 42,
                    "output_tokens": 8,
                    "total_tokens": 50,
                },
            ),
            AIMessage(
                content="approve",
                usage_metadata={
                    "input_tokens": 61,
                    "output_tokens": 3,
                    "total_tokens": 64,
                },
            ),
        ]
    )


class _EvidenceCallback(BaseCallbackHandler):
    """Turns LangGraph's own callback events into ``agentce_emit`` calls, using only their payload."""

    def __init__(self, emitter: agentce_emit.Emitter) -> None:
        self._emitter = emitter
        self._model_name = "scripted-chat-model"
        self._tool_args: Any = None
        self.last_tool_event: str | None = None

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        # The model's own class name, as LangChain serializes it -- not a value this script invented.
        self._model_name = serialized.get("name") or self._model_name

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        generation = response.generations[0][0]
        if not isinstance(generation, ChatGeneration) or not isinstance(
            generation.message, AIMessage
        ):
            return
        usage: dict[str, Any] = dict(generation.message.usage_metadata or {})
        self._emitter.emit_model_call(
            operation="chat",
            provider="scripted",
            model=self._model_name,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._tool_args = inputs if inputs is not None else input_str

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        result = json.loads(output.content) if isinstance(output.content, str) else output.content
        self.last_tool_event = self._emitter.emit_tool_call(
            name=output.name,
            server="mcp://credit-core.internal",
            protocol="mcp",
            args=self._tool_args,
            result=result,
            side_effect="write",
            effect_class="write",
        )


def _build_graph() -> Any:
    model = _scripted_model()

    def call_model(state: MessagesState) -> dict[str, list[Any]]:
        return {"messages": [model.invoke(state["messages"])]}

    builder = StateGraph(MessagesState)
    builder.add_node("model", call_model)
    builder.add_node("tools", ToolNode([record_credit_decision]))
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", tools_condition)
    builder.add_edge("tools", "model")
    return builder.compile()


def main() -> None:
    emitter = agentce_emit.auto()
    emitter.emit_session_start(environment="production", session_id=SESSION_ID)

    graph = _build_graph()
    callback = _EvidenceCallback(emitter)
    result = graph.invoke(
        {
            "messages": [
                (
                    "user",
                    f"Should we approve applicant {APPLICANT}'s credit application?",
                )
            ]
        },
        config={"callbacks": [callback]},
    )

    chosen = result["messages"][-1].content
    tool_event = callback.last_tool_event
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
