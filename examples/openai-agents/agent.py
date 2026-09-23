"""Runnable openai-agents example (SPEC 13.4 AX-3/AX-4): really imports and runs the openai-agents
SDK's ``Agent``/``Runner`` against the SDK's own deterministic test double,
``agents.testing.ScriptedModel``, then emits the evidence agentce-emit records from what that real
run actually produced.

The script builds one real ``@function_tool`` for the credit-decision tool, scripts a
``ScriptedModel`` so the agent calls that tool and then answers, and drives it through
``Runner.run_sync``. Every ``agentce_emit`` call below reads its values off the resulting
``RunResult`` (model usage, tool name/args/result) instead of hardcoding them elsewhere.
``ScriptedModel`` needs no network access or API key, so the whole run, and the evidence it
produces, is offline and deterministic.

``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh), so wiring it in never
changes behaviour until it is switched on. The bundle is flushed at process exit.
"""

from __future__ import annotations

import json

from agents import Agent, Runner, function_tool
from agents.items import ToolCallItem, ToolCallOutputItem
from agents.testing import ScriptedModel, assistant_message, function_call
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall

import agentce_emit

APPLICANT = "fictional-0001"
TOOL_NAME = "credit.record_decision"
TOOL_CALL_ID = "call-credit-1"


@function_tool(name_override=TOOL_NAME)
def record_credit_decision(applicant: str) -> dict[str, str]:
    """Record a credit decision for an applicant in the (fictional) credit-core system."""
    return {"status": "recorded", "applicant": applicant}


def _build_agent() -> tuple[Agent[None], ScriptedModel]:
    # Two scripted turns: the agent calls the tool, then answers once it sees the tool's result.
    model = ScriptedModel(
        steps=[
            {
                "output": [
                    function_call(
                        TOOL_NAME, {"applicant": APPLICANT}, call_id=TOOL_CALL_ID
                    )
                ],
                "usage": Usage(
                    requests=1, input_tokens=612, output_tokens=24, total_tokens=636
                ),
            },
            {
                "output": [assistant_message(f"Recommend approval for {APPLICANT}.")],
                "usage": Usage(
                    requests=1, input_tokens=684, output_tokens=96, total_tokens=780
                ),
            },
        ]
    )
    agent: Agent[None] = Agent(
        name="credit-underwriter",
        instructions="Decide whether to approve the applicant's credit application.",
        tools=[record_credit_decision],
        model=model,
    )
    return agent, model


def main() -> None:
    emitter = agentce_emit.auto()
    emitter.emit_session_start(environment="production", session_id="openai-agents-1")

    agent, model = _build_agent()
    result = Runner.run_sync(
        agent, f"Should we approve applicant {APPLICANT}'s credit application?"
    )

    # ScriptedModel never talks to a hosted model, so the honest "model" identity here is the real
    # class that executed the run, not a fabricated hosted-model name.
    model_name = f"{type(model).__module__}.{type(model).__qualname__}"
    for raw_response in result.raw_responses:
        emitter.emit_model_call(
            operation="chat",
            model=model_name,
            provider="openai-agents",
            input_tokens=raw_response.usage.input_tokens,
            output_tokens=raw_response.usage.output_tokens,
        )

    tool_call = next(
        item for item in result.new_items if isinstance(item, ToolCallItem)
    )
    tool_output = next(
        item for item in result.new_items if isinstance(item, ToolCallOutputItem)
    )
    assert isinstance(tool_call.raw_item, ResponseFunctionToolCall)
    tool_event = emitter.emit_tool_call(
        name=tool_call.tool_name or TOOL_NAME,
        server="mcp://credit-core.internal",
        protocol="mcp",
        args=json.loads(tool_call.raw_item.arguments),
        result=tool_output.output,
        side_effect="write",
        effect_class="write",
    )

    emitter.emit_decision(
        decision_type="dom:CreditDecision",
        affects_natural_person=True,
        ai_role="recommendation",
        oversight_modality="review_before",
        chosen="approve",
        refs={"executed_by": f"agentce:event/{tool_event}"} if tool_event else None,
    )
    emitter.emit_session_end(end_reason="completed", session_id="openai-agents-1")


if __name__ == "__main__":
    main()
