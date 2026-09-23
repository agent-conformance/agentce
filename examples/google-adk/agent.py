"""Runs a real Google Agent Development Kit (ADK) agent for one credit-decision turn (SPEC 13.4 AX-4).

This imports and runs ``google.adk`` itself: a real ``LlmAgent``, a real Python function tool, and ADK's
own ``InMemoryRunner``. The only stand-in is the model backend. ``ScriptedLlm`` below subclasses ADK's
public extension point, ``google.adk.models.base_llm.BaseLlm`` (the same base every real model backend,
e.g. Gemini or Claude, subclasses), and returns a scripted, deterministic sequence of responses instead of
calling out to a model API. That keeps the example offline and keyless while every tool call, model call,
and event still comes from ADK's own code paths.

The evidence emitted below is read back off the events ADK's runner actually produced, not out of values
already known independently.

``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh), so wiring it in never
changes behaviour until it is switched on. The bundle is flushed at process exit.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import agentce_emit
from google.adk.agents import LlmAgent
from google.adk.events import Event
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

APPLICANT = "fictional-0001"
APP_NAME = "credit-google-adk"
SESSION_ID = "google-adk-1"


def credit_record_decision(applicant: str) -> dict[str, str]:
    """Records a credit decision for the given applicant in the credit-core system."""
    return {"status": "recorded"}


# ADK registers and advertises a FunctionTool under func.__name__. A Gemini FunctionDeclaration name
# may contain dots, but a Python `def` name may not, so this is set after definition.
credit_record_decision.__name__ = "credit.record_decision"


class ScriptedLlm(BaseLlm):
    """A deterministic, network-free ``BaseLlm``: no model API, no API key.

    ADK drives every model backend through this same abstract method, feeding the tool's result back as
    the next turn's request. The response does not depend on ``llm_request``, only on how many turns have
    happened so far, which is what makes the two-turn script (function call, then final text) deterministic.
    """

    model: str = "scripted-credit-1"
    _calls: int = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._calls += 1
        if self._calls == 1:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name=credit_record_decision.__name__,
                                args={"applicant": APPLICANT},
                            )
                        )
                    ],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        else:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=f"Recommend approval for {APPLICANT}.")],
                ),
                finish_reason=types.FinishReason.STOP,
            )


async def run_agent(model: ScriptedLlm) -> list[Event]:
    """Builds a real LlmAgent over ``model`` and runs one turn through ADK's own InMemoryRunner."""
    agent = LlmAgent(
        name="credit_agent",
        model=model,
        instruction="Decide whether to approve or deny the applicant's credit application.",
        tools=[credit_record_decision],
    )
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    await runner.session_service.create_session(
        app_name=APP_NAME, user_id="underwriting", session_id=SESSION_ID
    )
    return [
        event
        async for event in runner.run_async(
            user_id="underwriting",
            session_id=SESSION_ID,
            new_message=types.Content(
                role="user",
                parts=[
                    types.Part(
                        text=f"Should we approve applicant {APPLICANT}'s credit application?"
                    )
                ],
            ),
        )
    ]


def main() -> None:
    emitter = agentce_emit.auto()
    emitter.emit_session_start(environment="production", session_id=SESSION_ID)

    model = ScriptedLlm()
    events = asyncio.run(run_agent(model))

    # Every event ScriptedLlm itself produced (role="model"): one per real generate_content_async call.
    for event in (e for e in events if e.content and e.content.role == "model"):
        usage = event.usage_metadata
        emitter.emit_model_call(
            operation="chat",
            provider="google-adk",
            model=model.model,
            input_tokens=usage.prompt_token_count if usage else None,
            output_tokens=usage.candidates_token_count if usage else None,
        )

    call_event = next(e for e in events if e.get_function_calls())
    result_event = next(e for e in events if e.get_function_responses())
    call = call_event.get_function_calls()[0]
    result = result_event.get_function_responses()[0]
    assert (
        call.name is not None
    )  # google.genai.types.FunctionCall.name is Optional; this one is scripted
    tool = emitter.emit_tool_call(
        name=call.name,
        server="mcp://credit-core.internal",
        protocol="mcp",
        args=call.args,
        result=result.response,
        side_effect="write",
        effect_class="write",
    )

    emitter.emit_decision(
        decision_type="dom:CreditDecision",
        affects_natural_person=True,
        ai_role="recommendation",
        oversight_modality="review_before",
        chosen="approve",
        refs={"executed_by": f"agentce:event/{tool}"} if tool else None,
    )
    emitter.emit_session_end(end_reason="completed", session_id=SESSION_ID)


if __name__ == "__main__":
    main()
