"""Semantic Kernel example (SPEC 13.4 AX-3/AX-4): really imports and runs Semantic Kernel, offline
and keyless.

This script builds a real ``semantic_kernel.Kernel`` around a real, custom
``semantic_kernel.connectors.ai.chat_completion_client_base.ChatCompletionClientBase`` -- Semantic
Kernel's own, currently-documented extension point for a chat-completion backend; every built-in
connector (``OpenAIChatCompletionBase`` and friends) subclasses exactly this base, confirmed by
reading the installed semantic-kernel==1.44.1 source. ``ScriptedChatCompletion`` overrides its
``_inner_get_chat_message_contents`` hook -- the method every real connector implements to perform one
completion round -- returning one function-call response, then a final text response: no network, no
API key. Semantic Kernel's own auto-invoke loop (``ChatCompletionClientBase.get_chat_message_contents``,
entered through ``Kernel.invoke`` on a ``KernelFunctionFromPrompt``) calls that hook repeatedly and,
on seeing the function call, really invokes the real kernel plugin function ``record_credit_decision``
(registered on a plugin class with Semantic Kernel's own ``@kernel_function`` decorator) through
``Kernel.invoke_function_call`` -- not called directly by this script.

Evidence is not hand-assembled after the fact: a ``function_invocation`` filter is registered with
``Kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, ...)`` -- Semantic Kernel's own, documented
middleware pipeline (see ``filters/kernel_filters_extension.py`` and
``functions/kernel_function.py::KernelFunction.invoke``, both read from the installed source). That one
filter wraps *every* kernel-function invocation made through this kernel: both the top-level chat/prompt
function (``context.function.metadata.is_prompt`` is True) and the nested ``record_credit_decision``
call the auto-invoke loop performs -- which runs through that exact same ``KernelFunction.invoke`` call
stack, so the identical filter fires for it too, with ``is_prompt`` False. After ``await next(context)``
runs the real call, the filter turns only the framework's own post-invocation payload into
``agentce_emit`` calls:

- For the prompt function: the real ``ChatMessageContent`` objects Semantic Kernel itself collected --
  in ``context.result.metadata["messages"]`` (the auto-invoke loop's own ``ChatHistory``, which holds
  every round that produced a function call) and in ``context.result.value`` (the final round, which
  the loop returns directly without adding it to that history) -- become one ``ModelCall`` per real
  completion round, reading each message's own ``ai_model_id`` and ``usage`` metadata (the same fields
  a real connector, e.g. ``OpenAIChatCompletionBase._get_metadata_from_chat_response``, sets).
- For the tool function: ``context.arguments`` (the real, parsed call arguments) and
  ``context.result.value`` (the real tool return value) become the ``ToolCall``.

``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh); the bundle is flushed at
process exit. Nothing here makes a network call: ``ScriptedChatCompletion`` never constructs an HTTP
client (it only imports ``CompletionUsage``, a plain data type, from the OpenAI connector module), and
Semantic Kernel's own OpenTelemetry instrumentation has no exporter configured in this process, so no
spans leave it either. No API key is read or required.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import PrivateAttr

import agentce_emit
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai import (
    CompletionUsage,
    FunctionChoiceBehavior,
    PromptExecutionSettings,
)
from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
from semantic_kernel.contents import ChatHistory, ChatMessageContent
from semantic_kernel.contents.function_call_content import FunctionCallContent
from semantic_kernel.contents.text_content import TextContent
from semantic_kernel.contents.utils.author_role import AuthorRole
from semantic_kernel.filters import FilterTypes, FunctionInvocationContext
from semantic_kernel.functions.kernel_function_decorator import kernel_function
from semantic_kernel.functions.kernel_function_from_prompt import KernelFunctionFromPrompt

APPLICANT = "fictional-0001"
SESSION_ID = "semantic-kernel-1"
MODEL_NAME = "scripted-credit-model"


class ScriptedChatCompletion(ChatCompletionClientBase):
    """A canned, offline ``ChatCompletionClientBase``: one tool call, then a final answer.

    ``_inner_get_chat_message_contents`` is the real per-round hook the base class documents for
    subclasses to implement (SPEC: see module docstring); it tracks its own call count rather than
    parsing the chat history back, since it knows exactly what it will say at each round. Each
    returned ``ChatMessageContent`` carries ``ai_model_id`` and a ``usage`` entry in ``metadata`` the
    same way a real connector's response does, so the evidence filter reads only real, framework-shaped
    data -- never a value this script invented outside that shape.
    """

    SUPPORTS_FUNCTION_CALLING = True
    _calls: int = PrivateAttr(default=0)

    async def _inner_get_chat_message_contents(
        self,
        chat_history: ChatHistory,
        settings: PromptExecutionSettings,
    ) -> list[ChatMessageContent]:
        self._calls += 1
        if self._calls == 1:
            items: list[Any] = [
                FunctionCallContent(
                    id="call-1",
                    name="credit_core-record_credit_decision",
                    function_name="record_credit_decision",
                    plugin_name="credit_core",
                    arguments={"applicant": APPLICANT},
                )
            ]
            usage = CompletionUsage(prompt_tokens=42, completion_tokens=8)
        else:
            items = [TextContent(text="approve")]
            usage = CompletionUsage(prompt_tokens=61, completion_tokens=3)
        return [
            ChatMessageContent(
                role=AuthorRole.ASSISTANT,
                items=items,
                ai_model_id=self.ai_model_id,
                metadata={"usage": usage},
            )
        ]

    # No override of `_inner_get_streaming_chat_message_contents`: this example never streams, and
    # the base class's own default already raises NotImplementedError for that path.


class CreditCorePlugin:
    """A real kernel plugin: one function, registered with Semantic Kernel's own decorator."""

    @kernel_function(name="record_credit_decision", description="Record a credit decision for an applicant.")
    def record_credit_decision(self, applicant: str) -> dict[str, str]:
        """Record a credit decision for an applicant against the (fictional) credit-core system."""
        return {"status": "recorded"}


def _model_call_messages(result: Any) -> list[ChatMessageContent]:
    """The real assistant ``ChatMessageContent`` objects the auto-invoke loop actually produced.

    ``ChatCompletionClientBase.get_chat_message_contents`` (SPEC: see module docstring) adds every
    completion round that contained a function call to its ``chat_history`` -- kept at
    ``context.result.metadata["messages"]`` -- but returns the final, function-call-free round
    directly as ``context.result.value`` *without* adding it to that history. Both are read, in
    order, de-duplicated by object identity so a message is never reported twice.
    """
    seen: set[int] = set()
    messages: list[ChatMessageContent] = []
    candidates: list[Any] = []
    metadata = result.metadata if result is not None else None
    history = metadata.get("messages") if metadata else None
    if isinstance(history, ChatHistory):
        candidates.extend(history.messages)
    if result is not None and isinstance(result.value, list):
        candidates.extend(result.value)
    for message in candidates:
        if not isinstance(message, ChatMessageContent) or message.role != AuthorRole.ASSISTANT:
            continue
        if id(message) in seen:
            continue
        seen.add(id(message))
        messages.append(message)
    return messages


async def _run() -> None:
    emitter = agentce_emit.auto()
    emitter.emit_session_start(environment="production", session_id=SESSION_ID)

    tool_events: list[str] = []

    async def _function_invocation_filter(
        context: FunctionInvocationContext,
        # Named `next`, not `next_filter`: `Kernel.construct_call_stack` (filters/kernel_filters_extension.py)
        # binds the next link in the chain with `partial(filter, next=stack[0])`, a keyword argument, so a
        # filter callable must accept a parameter named exactly `next` to receive it.
        next: Callable[[FunctionInvocationContext], Awaitable[None]],
    ) -> None:
        """Turns Semantic Kernel's own post-invocation payload into ``agentce_emit`` calls."""
        await next(context)
        if context.function.metadata.is_prompt:
            for message in _model_call_messages(context.result):
                usage = (message.metadata or {}).get("usage")
                emitter.emit_model_call(
                    operation="chat",
                    provider="scripted",
                    model=message.ai_model_id,
                    input_tokens=usage.prompt_tokens if usage else None,
                    output_tokens=usage.completion_tokens if usage else None,
                )
            return
        result_value = context.result.value if context.result is not None else None
        event_id = emitter.emit_tool_call(
            name=context.function.name,
            server="mcp://credit-core.internal",
            protocol="mcp",
            args=dict(context.arguments) if context.arguments else None,
            result=result_value,
            side_effect="write",
            effect_class="write",
        )
        if event_id:
            tool_events.append(event_id)

    kernel = Kernel()
    kernel.add_service(ScriptedChatCompletion(ai_model_id=MODEL_NAME))
    kernel.add_plugin(CreditCorePlugin(), plugin_name="credit_core")
    kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, _function_invocation_filter)

    settings = PromptExecutionSettings(function_choice_behavior=FunctionChoiceBehavior.Auto())
    chat_function = KernelFunctionFromPrompt(
        function_name="credit_decision",
        plugin_name="credit_analyst",
        prompt=f"Should we approve applicant {APPLICANT}'s credit application?",
        prompt_execution_settings=settings,
    )
    result = await kernel.invoke(function=chat_function)

    completions = result.value if result is not None else []
    chosen = completions[-1].content if completions else ""
    tool_event = tool_events[-1] if tool_events else None
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
