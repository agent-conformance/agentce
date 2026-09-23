"""CrewAI example (SPEC 13.4 AX-3/AX-4): really imports and runs CrewAI, offline and keyless.

This script builds a real ``crewai.Agent``/``crewai.Task``/``crewai.Crew`` for one credit-decision
scenario and actually ``.kickoff()``s it. The agent's model is ``ScriptedLLM``, a subclass of CrewAI's
own ``crewai.llms.base_llm.BaseLLM`` -- the framework's public, documented extension point for a
custom LLM backend ("Users can extend this class to create custom LLM implementations that don't rely
on litellm's authentication mechanism") -- whose ``call()`` is fully scripted and deterministic: no
network, no API key. CrewAI's own agent executor is a ReAct text loop (see
``crewai/agents/crew_agent_executor.py`` and ``crewai/agents/parser.py``): it calls ``call()`` and
parses the returned text for ``Action:``/``Action Input:`` or ``Final Answer:``. On its first call,
``ScriptedLLM`` returns an action naming the real ``record_credit_decision`` tool; CrewAI's own
``ToolUsage`` then parses that, invokes the tool for real, and the executor calls back with the
observation; on the second call ``ScriptedLLM`` returns a final recommendation.

Evidence is not hand-assembled after the fact: a ``crewai.events.BaseEventListener`` subscribes to
CrewAI's own event bus (``LLMCallCompletedEvent``, ``ToolUsageFinishedEvent``) and turns its real
payloads into ``agentce_emit`` calls, using only the data those events carry -- the same
emit-from-the-framework's-own-event-payload principle the LangGraph example applies to LangChain's
callback handler. CrewAI's event bus runs sync handlers on a background thread pool
(``crewai/events/event_bus.py``), so this module waits on a ``threading.Event`` set by the final
handler before reading state collected from it.

``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh); the bundle is flushed
at process exit. CrewAI's own install-telemetry ping (``crewai/__init__.py``) is disabled by
environment variable before import, so this run makes zero network calls.
"""

from __future__ import annotations

import ast
import os
import threading
from typing import Any

# Disable CrewAI's own install-telemetry ping before crewai is imported (zero network, SPEC 13.4 AX-4).
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

import agentce_emit
from crewai import Agent, Crew, Task
from crewai.events import BaseEventListener
from crewai.events.event_bus import CrewAIEventsBus
from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallType
from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent
from crewai.llms.base_llm import BaseLLM
from crewai.tools import tool

APPLICANT = "fictional-0001"
SESSION_ID = "crewai-1"
FINAL_ANSWER = "approve"


@tool
def record_credit_decision(applicant: str) -> dict[str, str]:
    """Record a credit decision for an applicant against the (fictional) credit-core system."""
    return {"status": "recorded"}


class ScriptedLLM(BaseLLM):
    """A canned, offline ``crewai.llms.base_llm.BaseLLM``: one tool call, then a final answer.

    CrewAI's default agent executor never passes ``tools``/``available_functions`` into ``call()``
    (confirmed by reading ``crewai/utilities/agent_utils.py::get_llm_response`` in the pinned
    crewai==1.6.1 source): those two parameters are only populated by native provider ``call()``
    implementations used outside the standard ``Agent``/``Task``/``Crew`` executor. So this
    implementation follows the ReAct text protocol CrewAI's executor actually speaks -- the same
    protocol its own default (litellm-backed) ``LLM`` class produces -- and lets CrewAI's own
    ``ToolUsage`` invoke the real tool and emit its own events. It tracks its own call count rather
    than parsing the message history back, since it knows exactly what it will say at each turn.
    """

    def __init__(self) -> None:
        super().__init__(model="scripted-credit-model", provider="scripted")
        self._calls = 0

    def call(
        self,
        messages: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        callbacks: list[Any] | None = None,
        available_functions: dict[str, Any] | None = None,
        from_task: Any | None = None,
        from_agent: Any | None = None,
        response_model: Any | None = None,
    ) -> str:
        self._emit_call_started_event(
            messages=messages,
            tools=tools,
            callbacks=callbacks,
            available_functions=available_functions,
            from_task=from_task,
            from_agent=from_agent,
        )
        self._calls += 1
        if self._calls == 1:
            text = (
                "Thought: I should record the credit decision before recommending an outcome.\n"
                f"Action: {record_credit_decision.name}\n"
                f'Action Input: {{"applicant": "{APPLICANT}"}}'
            )
        else:
            text = f"Thought: The decision is recorded.\nFinal Answer: {FINAL_ANSWER}"
        self._emit_call_completed_event(
            response=text,
            call_type=LLMCallType.LLM_CALL,
            from_task=from_task,
            from_agent=from_agent,
            messages=messages,
        )
        return text


class _EvidenceListener(BaseEventListener):
    """Turns CrewAI's own event-bus payloads into ``agentce_emit`` calls, using only their data."""

    def __init__(self, emitter: agentce_emit.Emitter) -> None:
        self._emitter = emitter
        self.last_tool_event: str | None = None
        #: Sync handlers run on the event bus's background thread pool (SPEC: see module docstring).
        #: These are set once their respective handler has run, so ``main`` can wait for both instead
        #: of racing the background thread for state (``last_tool_event``) collected from it.
        self.tool_done = threading.Event()
        self.done = threading.Event()
        super().__init__()

    def setup_listeners(self, crewai_event_bus: CrewAIEventsBus) -> None:
        @crewai_event_bus.on(LLMCallCompletedEvent)
        def _on_llm_call_completed(source: Any, event: LLMCallCompletedEvent) -> None:
            self._emitter.emit_model_call(
                operation="chat", provider="scripted", model=event.model
            )
            if isinstance(event.response, str) and "Final Answer:" in event.response:
                self.done.set()

        @crewai_event_bus.on(ToolUsageFinishedEvent)
        def _on_tool_finished(source: Any, event: ToolUsageFinishedEvent) -> None:
            # CrewAI stringifies the tool's return value with str() before putting it on the event
            # (crewai/tools/tool_usage.py::_format_result); literal_eval recovers the real dict the
            # tool actually returned rather than re-deriving it from scratch.
            try:
                result = (
                    ast.literal_eval(event.output)
                    if isinstance(event.output, str)
                    else event.output
                )
            except (ValueError, SyntaxError):
                result = event.output
            self.last_tool_event = self._emitter.emit_tool_call(
                name=event.tool_name,
                server="mcp://credit-core.internal",
                protocol="mcp",
                args=event.tool_args,
                result=result,
                side_effect="write",
                effect_class="write",
            )
            self.tool_done.set()


def main() -> None:
    emitter = agentce_emit.auto()
    emitter.emit_session_start(environment="production", session_id=SESSION_ID)

    listener = _EvidenceListener(emitter)
    agent = Agent(
        role="Credit Analyst",
        goal="Decide whether to approve or deny a credit application.",
        backstory=(
            "An experienced credit analyst who records every decision in the credit-core system "
            "before recommending an outcome."
        ),
        llm=ScriptedLLM(),
        tools=[record_credit_decision],
        verbose=False,
    )
    task = Task(
        description=f"Should we approve applicant {APPLICANT}'s credit application?",
        expected_output="A one-word recommendation: approve or deny.",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    result = crew.kickoff()

    # See _EvidenceListener: wait for the background-thread handlers to finish before reading state
    # collected from them (last_tool_event).
    listener.tool_done.wait(timeout=10)
    listener.done.wait(timeout=10)

    chosen = result.raw.strip()
    tool_event = listener.last_tool_event
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
