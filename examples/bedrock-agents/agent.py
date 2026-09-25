"""AWS Bedrock Agents example (SPEC 13.4 AX-3/AX-4): really calls the boto3 SDK, offline and keyless.

Unlike the other framework examples, a Bedrock Agent's orchestration loop runs *inside AWS*, not in
this process: there is no local graph/kernel to invoke. So this script is a real client of the AWS
SDK -- it builds a real ``boto3.client("bedrock-agent-runtime")`` and calls its real
``invoke_agent`` API -- but the wire call is replaced by AWS's own first-party, documented, offline
test double for ``boto3``: ``botocore.stub.Stubber`` (pinned here via ``boto3``/``botocore``
1.43.102; see ``botocore/stub.py`` in the installed package). ``Stubber`` registers a
``before-call`` handler that short-circuits ``BaseClient._make_api_call`` before it ever reaches
``self._make_request``/the HTTP layer (see ``botocore/client.py::_make_api_call``: when the
``before-call`` event returns a response, the real request path is skipped entirely) -- so this run
makes zero network calls. ``AWS_ACCESS_KEY_ID``/``AWS_SECRET_ACCESS_KEY``/``AWS_DEFAULT_REGION`` are
set below to obviously-fake dummy values only because ``boto3.client(...)`` requires *some* region
and credential values to be present at construction time; they are never used to sign or send a
request (signing happens later in the real request path, which ``Stubber`` never reaches), and they
are not real credentials.

The stubbed response mirrors AWS's own real ``InvokeAgent`` response shape exactly, per the
installed service model (``botocore/data/bedrock-agent-runtime/2023-07-26/service-2.json``, gzipped
in the package): ``InvokeAgentResponse.completion`` is a ``ResponseStream`` (``"eventstream": true``)
whose modeled shape (a ``trace`` member wrapping a ``TracePart``, and a ``chunk`` member wrapping a
``PayloadPart``) describes *one* event, not the multi-event stream a real client receives when it
iterates ``response["completion"]``. ``botocore.stub.Stubber.add_response`` validates a stubbed
response against exactly that per-event shape (``botocore/stub.py::_validate_operation_response``),
so its stock validator cannot accept a real multi-event ``completion`` list -- this is a known gap in
Stubber's eventstream support, not a detail this script invents. ``_EventStreamStubber`` below is a
thin ``Stubber`` subclass that keeps every other real behaviour (the before-call queue, call-order
assertion, request-parameter assertion via ``before-parameter-build``,
``assert_no_pending_responses``) and only replaces the one broken check with a more correct one:
validate *each* event in the list against the real per-event ``ResponseStream`` shape, which is
exactly what a real ``EventStream`` yields as you iterate it.

Each trace event's ``orchestrationTrace`` names the real Bedrock Agents trace fields
(``modelInvocationInput``/``modelInvocationOutput`` for the foundation-model step,
``invocationInput.actionGroupInvocationInput`` for the action-group call naming
``record_credit_decision``, ``observation.actionGroupInvocationOutput`` for its result) --
``record_credit_decision`` conceptually *is* the action-group Lambda AWS would invoke server-side;
since ``Stubber`` replaces the whole HTTP call, there is no real Lambda invocation to hook, so this
script scripts the trace events AWS's own service would emit around that call. ``main`` parses the
(stubbed) ``EventStream`` the way a real Bedrock Agents client is documented to: iterating
``response["completion"]`` and checking each event dict's ``trace``/``chunk`` keys -- that parsing
logic is the real production code path; only the wire call is faked. ``agentce_emit.auto()`` is
active only when ``AGENTCE_EMIT=1`` (set by run.sh); the bundle is flushed at process exit.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

# boto3's client constructor requires *some* region and credential values to be present; these are
# obviously-fake dummy values, never used (Stubber's before-call short-circuit means the real
# request-signing path, which is what would read them, never runs -- see the module docstring).
os.environ.setdefault("AWS_ACCESS_KEY_ID", "AKIAFICTIONAL0000000")
os.environ.setdefault(
    "AWS_SECRET_ACCESS_KEY", "fictional/secret/access/key/not-real-0000000000"
)
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import agentce_emit
import boto3
from botocore.stub import Stubber
from botocore.validate import validate_parameters

APPLICANT = "fictional-0001"
SESSION_ID = "bedrock-agents-1"
FINAL_ANSWER = "approve"
AGENT_ID = "AGENTCE001"
AGENT_ALIAS_ID = "TSTALIAS01"
FOUNDATION_MODEL = "anthropic.claude-3-5-sonnet-20241022-v2:0"
ACTION_GROUP_NAME = "record_credit_decision"
API_PATH = "/record-credit-decision"
INPUT_TEXT = f"Should we approve applicant {APPLICANT}'s credit application?"
DECIDE_TRACE_ID = "trace-orchestration-1"
ANSWER_TRACE_ID = "trace-orchestration-2"


class _EventStreamStubber(Stubber):
    """A ``Stubber`` that validates eventstream responses per-event (see module docstring).

    ``Stubber._validate_operation_response`` validates a stubbed response against the operation's
    output shape as a single structure. For ``InvokeAgentResponse.completion`` -- modeled as
    ``ResponseStream`` (``"eventstream": true`` in the service model) -- that shape describes one
    event, not the list of events a real client receives while iterating the stream. This override
    keeps every other real ``Stubber`` behaviour and validates each item in the ``completion`` list
    against that same real per-event shape instead of the whole list at once.
    """

    def _validate_operation_response(
        self, operation_name: str, service_response: dict[str, Any]
    ) -> None:
        output_shape = self.client.meta.service_model.operation_model(
            operation_name
        ).output_shape
        response = copy.copy(service_response)
        response.pop("ResponseMetadata", None)
        events = response.pop("completion", [])
        event_shape = output_shape.members["completion"]
        for event in events:
            validate_parameters(event, event_shape)
        for name, value in response.items():
            if name not in output_shape.members:
                raise ValueError(f"unknown top-level response member: {name}")
            validate_parameters(value, output_shape.members[name])
        for required in output_shape.metadata.get("required", []):
            if required not in service_response:
                raise ValueError(f"missing required response member: {required}")


def _trace_event(trace_body: dict[str, Any]) -> dict[str, Any]:
    """One ``TracePart`` stream event -- real field names per ``TracePart`` in the service model."""
    return {
        "trace": {
            "agentId": AGENT_ID,
            "agentAliasId": AGENT_ALIAS_ID,
            "sessionId": SESSION_ID,
            "trace": trace_body,
        }
    }


def _scripted_completion() -> list[dict[str, Any]]:
    """The scripted, offline ``EventStream`` events for this credit-decision scenario.

    Mirrors the real sequence a Bedrock Agent's orchestration loop produces with ``enableTrace``
    on: a model-invocation trace pair for the reasoning step that decides to call the action group,
    the action-group invocation naming ``record_credit_decision`` and its observation, a second
    model-invocation trace pair for the final recommendation, and the one ``chunk`` event carrying
    the agent's final response text (``InvokeAgent`` returns exactly one ``chunk`` for the whole
    interaction, per the service model's ``InvokeAgent`` operation documentation).
    """
    return [
        _trace_event(
            {
                "orchestrationTrace": {
                    "modelInvocationInput": {
                        "traceId": DECIDE_TRACE_ID,
                        "type": "ORCHESTRATION",
                        "foundationModel": FOUNDATION_MODEL,
                        "text": INPUT_TEXT,
                    }
                }
            }
        ),
        _trace_event(
            {
                "orchestrationTrace": {
                    "modelInvocationOutput": {
                        "traceId": DECIDE_TRACE_ID,
                        "rawResponse": {
                            "content": (
                                "I should record the credit decision before recommending "
                                "an outcome."
                            )
                        },
                        "metadata": {"usage": {"inputTokens": 42, "outputTokens": 8}},
                    }
                }
            }
        ),
        _trace_event(
            {
                "orchestrationTrace": {
                    "invocationInput": {
                        "traceId": DECIDE_TRACE_ID,
                        "invocationType": "ACTION_GROUP",
                        "actionGroupInvocationInput": {
                            "actionGroupName": ACTION_GROUP_NAME,
                            "apiPath": API_PATH,
                            "verb": "POST",
                            "parameters": [
                                {
                                    "name": "applicant",
                                    "type": "string",
                                    "value": APPLICANT,
                                }
                            ],
                        },
                    }
                }
            }
        ),
        _trace_event(
            {
                "orchestrationTrace": {
                    "observation": {
                        "traceId": DECIDE_TRACE_ID,
                        "type": "ACTION_GROUP",
                        "actionGroupInvocationOutput": {
                            "text": json.dumps({"status": "recorded"}),
                        },
                    }
                }
            }
        ),
        _trace_event(
            {
                "orchestrationTrace": {
                    "modelInvocationInput": {
                        "traceId": ANSWER_TRACE_ID,
                        "type": "ORCHESTRATION",
                        "foundationModel": FOUNDATION_MODEL,
                        "text": "The decision is recorded. What is the final recommendation?",
                    }
                }
            }
        ),
        _trace_event(
            {
                "orchestrationTrace": {
                    "modelInvocationOutput": {
                        "traceId": ANSWER_TRACE_ID,
                        "rawResponse": {"content": FINAL_ANSWER},
                        "metadata": {"usage": {"inputTokens": 61, "outputTokens": 3}},
                    }
                }
            }
        ),
        {"chunk": {"bytes": FINAL_ANSWER.encode("utf-8")}},
    ]


def _build_stubbed_client() -> tuple[Any, Stubber]:
    """A real ``bedrock-agent-runtime`` client with a real ``Stubber`` queued for one call."""
    client = boto3.client("bedrock-agent-runtime", region_name="us-east-1")
    stubber = _EventStreamStubber(client)
    response = {
        "completion": _scripted_completion(),
        "contentType": "application/json",
        "sessionId": SESSION_ID,
    }
    stubber.add_response(
        "invoke_agent",
        response,
        {
            "agentId": AGENT_ID,
            "agentAliasId": AGENT_ALIAS_ID,
            "sessionId": SESSION_ID,
            "inputText": INPUT_TEXT,
            "enableTrace": True,
        },
    )
    return client, stubber


def main() -> None:
    emitter = agentce_emit.auto()
    emitter.emit_session_start(environment="production", session_id=SESSION_ID)

    client, stubber = _build_stubbed_client()
    #: The foundation-model id for a trace's modelInvocationOutput, correlated by its own
    #: traceId (from the earlier modelInvocationInput event carrying the same id) -- exactly how a
    #: real Bedrock Agents client correlates trace steps in a session (SPEC: only data the real
    #: trace events carry, nothing invented here).
    model_by_trace: dict[str, str] = {}
    #: The action-group call named by an invocationInput event, correlated to its later observation
    #: by traceId the same way.
    tool_by_trace: dict[str, dict[str, Any]] = {}
    last_tool_event: str | None = None
    final_text: str | None = None

    with stubber:
        response = client.invoke_agent(
            agentId=AGENT_ID,
            agentAliasId=AGENT_ALIAS_ID,
            sessionId=SESSION_ID,
            inputText=INPUT_TEXT,
            enableTrace=True,
        )
        for event in response["completion"]:
            if "chunk" in event:
                final_text = event["chunk"]["bytes"].decode("utf-8")
                continue
            trace = event.get("trace", {}).get("trace", {})
            orchestration = trace.get("orchestrationTrace")
            if not orchestration:
                continue
            if "modelInvocationInput" in orchestration:
                mii = orchestration["modelInvocationInput"]
                model_by_trace[mii["traceId"]] = mii["foundationModel"]
            elif "modelInvocationOutput" in orchestration:
                mio = orchestration["modelInvocationOutput"]
                usage = mio.get("metadata", {}).get("usage", {})
                emitter.emit_model_call(
                    operation="chat",
                    provider="bedrock",
                    model=model_by_trace.get(mio["traceId"], FOUNDATION_MODEL),
                    input_tokens=usage.get("inputTokens"),
                    output_tokens=usage.get("outputTokens"),
                )
            elif "invocationInput" in orchestration:
                invocation = orchestration["invocationInput"]
                action_group = invocation.get("actionGroupInvocationInput")
                if action_group:
                    tool_by_trace[invocation["traceId"]] = {
                        "name": action_group["actionGroupName"],
                        "api_path": action_group["apiPath"],
                        "args": {
                            parameter["name"]: parameter["value"]
                            for parameter in action_group.get("parameters", [])
                        },
                    }
            elif "observation" in orchestration:
                observation = orchestration["observation"]
                output = observation.get("actionGroupInvocationOutput")
                if output:
                    tool = tool_by_trace.get(observation["traceId"], {})
                    last_tool_event = emitter.emit_tool_call(
                        name=tool.get("name", ACTION_GROUP_NAME),
                        server=f"bedrock-agent-action-group://{tool.get('api_path', API_PATH)}",
                        protocol="native",
                        args=tool.get("args"),
                        result=json.loads(output["text"]),
                        side_effect="write",
                        effect_class="write",
                    )
        stubber.assert_no_pending_responses()

    emitter.emit_decision(
        decision_type="dom:CreditDecision",
        affects_natural_person=True,
        ai_role="recommendation",
        oversight_modality="review_before",
        chosen=final_text or FINAL_ANSWER,
        refs={"executed_by": f"agentce:event/{last_tool_event}"}
        if last_tool_event
        else None,
    )
    emitter.emit_session_end(end_reason="completed", session_id=SESSION_ID)


if __name__ == "__main__":
    main()
