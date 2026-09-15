"""The AgentCE evidence emitter (SPEC §13.1, §13.4 AX-3).

``agentce-emit`` lets an agent emit canonical AgentCE evidence with one line: ``agentce_emit.auto()``
returns an :class:`Emitter` that is active when ``AGENTCE_EMIT=1`` and a no-op otherwise, so wiring it
in never changes behaviour until it is switched on. Every event is a CloudEvents 1.0 envelope with a
JSON-LD payload, labelled ``self_report`` (agent-side emission is a self-report, S-2), with content
referenced by hash rather than captured (SPEC R12) and ids derived deterministically. On flush the
emitter writes an evidence bundle (``events/*.jsonl`` + ``manifest.json``) that ``agentce validate``
accepts with zero quarantines. Standard library only; no network, no learned component.
"""

from __future__ import annotations

import atexit
import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

BASE_CONTEXT = "https://agent-conformance.org/contexts/evidence/v1"
DEFAULT_CONVENTION = "agentce-emit:1.0"
_VALID_CLASSES = frozenset({"self_report", "enforcement_point", "independent_system"})
_BASE = datetime.datetime(2026, 5, 1, 9, 0, 0, tzinfo=datetime.timezone.utc)


def _digest_ref(content: Any) -> str:
    """A content reference: the SHA-256 of the content, never the content itself (SPEC R12, S-9)."""
    raw = (
        content
        if isinstance(content, (bytes, bytearray))
        else json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class Emitter:
    """Collects canonical evidence events and, on flush, writes an evidence bundle.

    When ``active`` is false every ``emit_*`` call is a no-op and ``flush`` writes nothing, so a
    deployment can wire the emitter in unconditionally and switch it on with ``AGENTCE_EMIT=1``.
    """

    def __init__(
        self,
        *,
        out: Path,
        subject: str,
        source: str,
        source_class: str = "self_report",
        convention: str = DEFAULT_CONVENTION,
        active: bool = True,
    ) -> None:
        self.out = Path(out)
        self.subject = subject
        self.source = source
        self.source_class = (
            source_class if source_class in _VALID_CLASSES else "self_report"
        )
        self.convention = convention
        self.active = active
        self.events: list[dict[str, Any]] = []
        self._flushed = False

    # --- envelope ---------------------------------------------------------------------------------

    def _emit(self, event_type: str, payload: dict[str, Any]) -> str | None:
        if not self.active:
            return None
        seq = len(self.events)
        event_id = f"emit-{seq:06d}"
        moment = (_BASE + datetime.timedelta(seconds=seq)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        self.events.append(
            {
                "specversion": "1.0",
                "id": event_id,
                "source": self.source,
                "type": f"org.agent-conformance.evidence.{event_type}.v1",
                "time": moment,
                "subject": self.subject,
                "datacontenttype": "application/ld+json",
                "agentcesourceclass": self.source_class,
                "agentceconv": self.convention,
                "data": {"@context": BASE_CONTEXT, "@type": event_type, **payload},
            }
        )
        return event_id

    # --- events (SPEC §13.1 step 4) ---------------------------------------------------------------

    def emit_session_start(
        self, *, environment: str | None = None, session_id: str | None = None
    ) -> str | None:
        payload: dict[str, Any] = {}
        if session_id is not None:
            payload["session_id"] = session_id
        if environment is not None:
            payload["environment"] = environment
        return self._emit("SessionStart", payload)

    def emit_session_end(
        self, *, end_reason: str = "completed", session_id: str | None = None
    ) -> str | None:
        payload: dict[str, Any] = {"end_reason": end_reason}
        if session_id is not None:
            payload["session_id"] = session_id
        return self._emit("SessionEnd", payload)

    def emit_model_call(
        self,
        *,
        operation: str = "chat",
        model: str | None = None,
        provider: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> str | None:
        payload: dict[str, Any] = {"operation": operation}
        model_ref: dict[str, Any] = {}
        if provider is not None:
            model_ref["provider"] = provider
        if model is not None:
            model_ref["name"] = model
        if model_ref:
            payload["model"] = model_ref
        usage: dict[str, Any] = {}
        if input_tokens is not None:
            usage["input_tokens"] = input_tokens
        if output_tokens is not None:
            usage["output_tokens"] = output_tokens
        if usage:
            payload["usage"] = usage
        return self._emit("ModelCall", payload)

    def emit_tool_call(
        self,
        *,
        name: str,
        server: str | None = None,
        protocol: str | None = None,
        args: Any = None,
        result: Any = None,
        side_effect: str | None = None,
        effect_class: str | None = None,
        refs: dict[str, str] | None = None,
    ) -> str | None:
        tool: dict[str, Any] = {"name": name}
        if server is not None:
            tool["server"] = server
        if protocol in {"mcp", "a2a", "http", "native"}:
            tool["protocol"] = protocol
        payload: dict[str, Any] = {"tool": tool}
        if args is not None:
            payload["args_ref"] = _digest_ref(args)
        if result is not None:
            payload["result_ref"] = _digest_ref(result)
        if side_effect is not None:
            payload["side_effect"] = side_effect
        if effect_class is not None:
            payload["effect_class"] = effect_class
        if refs:
            payload["refs"] = refs
        return self._emit("ToolCall", payload)

    def emit_resource_access(
        self, *, uri: str, operation: str = "read", kind: str | None = None
    ) -> str | None:
        resource: dict[str, Any] = {"uri": uri}
        if kind is not None:
            resource["kind"] = kind
        return self._emit(
            "ResourceAccess", {"resource": resource, "operation": operation}
        )

    def emit_decision(
        self,
        *,
        decision_type: str,
        affects_natural_person: bool | None = None,
        ai_role: str | None = None,
        oversight_modality: str | None = None,
        chosen: str | None = None,
        refs: dict[str, str] | None = None,
    ) -> str | None:
        payload: dict[str, Any] = {"decision_type": decision_type}
        if affects_natural_person is not None:
            payload["affects_natural_person"] = affects_natural_person
        if ai_role is not None:
            payload["ai_role"] = ai_role
        if oversight_modality is not None:
            payload["oversight_modality"] = oversight_modality
        if chosen is not None:
            payload["chosen"] = chosen
        if refs:
            payload["refs"] = refs
        return self._emit("Decision", payload)

    def emit_instruction(
        self,
        *,
        source_class: str,
        content: Any = None,
        refs: dict[str, str] | None = None,
    ) -> str | None:
        payload: dict[str, Any] = {"source_class": source_class}
        if content is not None:
            payload["content_ref"] = _digest_ref(content)
        if refs:
            payload["refs"] = refs
        return self._emit("Instruction", payload)

    def emit_refusal(
        self, *, reason_class: str, refs: dict[str, str] | None = None
    ) -> str | None:
        payload: dict[str, Any] = {"reason_class": reason_class}
        if refs:
            payload["refs"] = refs
        return self._emit("Refusal", payload)

    def emit_incident(
        self, *, incident_class: str, incident_id: str | None = None
    ) -> str | None:
        payload: dict[str, Any] = {"incident_class": incident_class}
        if incident_id is not None:
            payload["incident_id"] = incident_id
        return self._emit("Incident", payload)

    # --- bundle -----------------------------------------------------------------------------------

    def flush(self) -> Path | None:
        """Write the collected events to an evidence bundle at ``out``; return the bundle path."""
        if not self.active or self._flushed or not self.events:
            return None
        self._flushed = True
        events_dir = self.out / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        stream = events_dir / "stream.jsonl"
        stream.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in self.events),
            encoding="utf-8",
        )
        digest = hashlib.sha256(stream.read_bytes()).hexdigest()
        manifest = {
            "agentce_bundle_version": 1,
            "sources": [
                {
                    "id": self.source,
                    "adapter": "agentce-emit",
                    "class": self.source_class,
                }
            ],
            "files": [{"path": "events/stream.jsonl", "sha256": digest}],
        }
        (self.out / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        return self.out

    def __enter__(self) -> Emitter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.flush()


def auto(**overrides: Any) -> Emitter:
    """One-line integration (SPEC §13.4 AX-3): an :class:`Emitter`, active iff ``AGENTCE_EMIT=1``.

    Reads ``AGENTCE_EMIT`` (activation), ``AGENTCE_EMIT_OUT`` (bundle directory),
    ``AGENTCE_EMIT_SUBJECT``, and ``AGENTCE_EMIT_SOURCE``; keyword overrides win. When active, the
    bundle is flushed at process exit, so ``import agentce_emit; agentce_emit.auto()`` plus a few
    ``emit_*`` calls is all an agent needs.
    """
    active = bool(overrides.get("active", os.environ.get("AGENTCE_EMIT") == "1"))
    emitter = Emitter(
        out=Path(
            overrides.get("out")
            or os.environ.get("AGENTCE_EMIT_OUT")
            or "agentce/bundles/auto"
        ),
        subject=str(
            overrides.get("subject")
            or os.environ.get("AGENTCE_EMIT_SUBJECT")
            or "agentce:subject/local"
        ),
        source=str(
            overrides.get("source")
            or os.environ.get("AGENTCE_EMIT_SOURCE")
            or "urn:agentce:emit:local"
        ),
        source_class=str(overrides.get("source_class", "self_report")),
        active=active,
    )
    if active:
        atexit.register(emitter.flush)
    return emitter
