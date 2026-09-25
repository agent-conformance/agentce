"""Vertex AI Agents example (SPEC 13.4 AX-3/AX-4): really calls ``google-genai``, offline and keyless.

Vertex AI Agents run against Google's hosted Gemini/Vertex endpoint -- there is no local graph to
invoke, unlike CrewAI or LangGraph. This script is therefore a real client of ``google-genai``
(``google.genai.Client``), the unified Python SDK that Vertex AI's own docs now point developers to
for the Gemini/agent model surface (``google-cloud-aiplatform``'s ``vertexai`` module is the legacy
wrapper; confirmed by reading the installed ``google-genai==2.25.0`` source, which is the package this
example depends on). It builds one real ``genai.Client(vertexai=True, ...)``, a real Python tool
function, and calls the SDK's own ``Models.generate_content`` -- the exact code path that decides
whether a model turn results in a real function call, per ``google.genai.models`` in the installed
source -- for one credit-decision turn.

Offline mechanism actually used (read from the installed source before writing this file):

The installed package ships an internal ``google.genai._replay_api_client.ReplayApiClient`` plus a
``Client(debug_config=DebugConfig(client_mode="replay", ...))`` hook that *looks* like an
official Stubber-equivalent. It is not one: the module is underscore-prefixed (private, per the
package's own convention -- contrast ``google.genai.client``, which is public), it is not exported
from ``google.genai.__init__``'s ``__all__``, and "replay" mode requires a pre-recorded
``ReplayFile`` fixture on disk whose request-matching logic (``_normalize_json_case``,
``_redact_request_headers``, project/location/UUID redaction, ...) exists to support Google's own
SDK conformance tests, not hand-authored consumer fixtures. So this is the SDK's internal test
harness, not a public, documented offline-testing utility -- the same conclusion the brief reaches
about needing to check installed source rather than assume.

What genuinely is a public, documented override point: ``google.genai.types.HttpOptions.httpx_client``
-- "A custom httpx client to be used for the request" (its own docstring, in the public ``types``
module) -- accepted by ``Client(http_options=...)``. Reading ``google.genai._api_client.BaseApiClient``
confirms that when ``http_options.httpx_client`` is set, it is used verbatim as ``self._httpx_client``,
and ``_request_once`` calls exactly ``self._httpx_client.send(...)`` to perform the real HTTP call --
no other path (the alternate ``google.auth`` ``AuthorizedSession`` path used for mTLS is explicitly
skipped whenever ``httpx_client`` is set; see ``BaseApiClient._use_google_auth_sync``). So this example
constructs its own ``httpx.Client(transport=httpx.MockTransport(...))`` -- ``httpx``'s own standard,
documented test-double transport -- and passes it in through that one public field. No
``unittest.mock.patch`` of any kind is used anywhere in this file: every override is a normal,
public constructor argument on real classes (``Client``, ``HttpOptions``, ``httpx.Client``,
``httpx.MockTransport``). The scripted transport below returns real Vertex ``generateContent`` REST
response bodies (the same camelCase wire shape ``google.genai.models._GenerateContentResponse_from_vertex``
expects); each is independently validated with the real ``types.GenerateContentResponse`` Pydantic
model (``model_validate``, using the SDK's own ``alias_generator=to_camel`` field aliasing) both to
build the evidence below and as a self-check that the scripted body is field-name-correct.

Zero real credentials: ``genai.Client`` needs *some* ``google.auth.credentials.Credentials`` object
before it will skip Application Default Credentials discovery (``BaseApiClient._access_token`` calls
``load_auth()`` -- which talks to the real ADC/metadata-server machinery -- only when
``self._credentials`` is unset). ``_OfflineCredentials`` below is an obviously-fake stand-in: its
token is a hardcoded placeholder string, and its ``refresh()`` raises if ever called (it never is,
because the token is never expired). ``project``/``location`` are equally obvious placeholders.

Automatic function calling (real, not manually driven): passing a plain, undecorated Python function
in ``GenerateContentConfig(tools=[record_credit_decision])`` is ``google-genai``'s own documented
mechanism for tool declaration -- the SDK derives the function's schema from its signature. With
automatic function calling enabled (the default whenever ``tools`` contains a plain callable),
``Models.generate_content`` (see the ``while remaining_remote_calls_afc > 0`` loop in
``google/genai/models.py``) itself calls ``self._generate_content`` a first time, and if the model's
response contains a ``function_call`` part, calls
``google.genai._extra_utils.invoke_function_from_dict_args`` -- which really invokes
``record_credit_decision(**args)`` -- and calls ``self._generate_content`` a second time with the
function's real result appended to the conversation, entirely inside the SDK's own code. This example
never calls ``record_credit_decision`` itself and never drives a manual two-turn loop: with the
transport faked, the SDK's real AFC loop runs to completion in-process, offline. (The SDK logs a
warning recommending ``Chat.send_message`` over direct ``Models.generate_content`` for AFC in typical
multi-turn chat use; both share the same AFC loop and, for this single-turn scenario,
``Models.generate_content`` alone is what returns ``automatic_function_calling_history`` directly on
the response, which is what this script reads for evidence.)

``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh); the bundle is flushed
at process exit.
"""

from __future__ import annotations

from typing import Any

import google.auth.credentials
import httpx

import agentce_emit
from google import genai
from google.genai import types

APPLICANT = "fictional-0001"
SESSION_ID = "vertex-agents-1"
MODEL = "gemini-2.0-flash-001"


def record_credit_decision(applicant: str) -> dict[str, str]:
    """Records a credit decision for an applicant against the (fictional) credit-core system."""
    return {"status": "recorded"}


class _OfflineCredentials(google.auth.credentials.Credentials):
    """An obviously-fake ``google.auth.credentials.Credentials``: no project, no service account.

    ``BaseApiClient._access_token`` only calls the real Application Default Credentials machinery
    (``load_auth()``) when the client has no credentials at all; giving it one with a pre-set,
    never-expiring token (``expiry`` stays ``None``, so ``expired`` is always ``False``) means
    ``get_token_from_credentials`` returns ``self.token`` directly and ``refresh()`` is never called.
    """

    def __init__(self) -> None:
        super().__init__()
        # Not a real credential; never sent anywhere real (transport is fully mocked below).
        self.token = "offline-example-token"

    def refresh(self, request: Any) -> None:
        raise AssertionError(
            "refresh() should never be called: _OfflineCredentials.token never expires"
        )


class _ScriptedVertexTransport:
    """An ``httpx.MockTransport`` handler: two real Vertex ``generateContent`` REST responses.

    Each call returns the next scripted response body, wired to ``genai.Client`` through
    ``HttpOptions.httpx_client`` (see the module docstring for why that is the real, public override
    point used here, and why it needed no ``unittest.mock.patch``). Every body is also independently
    parsed into a real ``types.GenerateContentResponse`` via ``model_validate`` and kept in
    ``self.responses``, so ``main`` can emit one ``ModelCall`` per real model turn using only fields
    that object actually carries -- the SDK's own ``generate_content`` return value only carries the
    *last* turn's response, not every intermediate one.
    """

    def __init__(self) -> None:
        self.responses: list[types.GenerateContentResponse] = []
        self._bodies: list[dict[str, Any]] = [
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": record_credit_decision.__name__,
                                        "args": {"applicant": APPLICANT},
                                    }
                                }
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "modelVersion": MODEL,
                "responseId": "vertex-agents-resp-1",
                "usageMetadata": {
                    "promptTokenCount": 42,
                    "candidatesTokenCount": 8,
                    "totalTokenCount": 50,
                },
            },
            {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "approve"}]},
                        "finishReason": "STOP",
                    }
                ],
                "modelVersion": MODEL,
                "responseId": "vertex-agents-resp-2",
                "usageMetadata": {
                    "promptTokenCount": 61,
                    "candidatesTokenCount": 3,
                    "totalTokenCount": 64,
                },
            },
        ]
        self._calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = self._bodies[self._calls]
        self._calls += 1
        self.responses.append(types.GenerateContentResponse.model_validate(body))
        return httpx.Response(200, json=body, request=request)


def _build_client(transport: _ScriptedVertexTransport) -> genai.Client:
    """A real ``genai.Client`` in Vertex mode, its transport replaced with ``transport`` (no network)."""
    mock_httpx_client = httpx.Client(transport=httpx.MockTransport(transport))
    return genai.Client(
        vertexai=True,
        project="fictional-project",
        location="us-central1",
        credentials=_OfflineCredentials(),
        http_options=types.HttpOptions(httpx_client=mock_httpx_client),
    )


def main() -> None:
    emitter = agentce_emit.auto()
    emitter.emit_session_start(environment="production", session_id=SESSION_ID)

    transport = _ScriptedVertexTransport()
    client = _build_client(transport)

    response = client.models.generate_content(
        model=MODEL,
        contents=f"Should we approve applicant {APPLICANT}'s credit application?",
        config=types.GenerateContentConfig(tools=[record_credit_decision]),
    )

    # One ModelCall per real turn the SDK's own AFC loop made (see _ScriptedVertexTransport).
    for turn in transport.responses:
        usage = turn.usage_metadata
        emitter.emit_model_call(
            operation="chat",
            provider="vertex-ai",
            model=turn.model_version,
            input_tokens=usage.prompt_token_count if usage else None,
            output_tokens=usage.candidates_token_count if usage else None,
        )

    # automatic_function_calling_history is populated by the SDK's own AFC loop (google/genai/models.py)
    # from the real Content/Part/FunctionCall/FunctionResponse objects it built during that loop.
    call: types.FunctionCall | None = None
    result: types.FunctionResponse | None = None
    for content in response.automatic_function_calling_history or []:
        for part in content.parts or []:
            if part.function_call is not None:
                call = part.function_call
            if part.function_response is not None:
                result = part.function_response

    tool_event = None
    if call is not None and result is not None:
        assert (
            call.name is not None
        )  # FunctionCall.name is Optional; this one is scripted
        tool_event = emitter.emit_tool_call(
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
        chosen=response.text,
        refs={"executed_by": f"agentce:event/{tool_event}"} if tool_event else None,
    )
    emitter.emit_session_end(end_reason="completed", session_id=SESSION_ID)


if __name__ == "__main__":
    main()
