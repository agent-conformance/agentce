"""find_chokepoints (SPEC 13.3.3 phase B, 13.1 STEP 1): inventory an agent codebase's chokepoints.

Scans the Python sources under ``--repo`` for the points where evidence must be emitted -- model
calls, tool and resource calls, authorization, credential acquisition, human approval, memory and
retrieval, component loading at startup, consequential decisions, incident and disclosure paths, and
(for the Conduct overlay, SPEC 7.7.5) instruction-entry and refusal paths. Output is sorted by path,
line, and kind; each entry carries ``kind``, ``confidence``, and ``enforcement_point_present``.

The scanner is deterministic, offline, and model-free (rule S-4) and minimises content (rule S-9): it
records only the path, line, kind, and confidence -- never the code, a prompt, or any secret.
Exit code 0 when it finds chokepoints, 1 when it finds none (an agent with no chokepoints is
suspicious and worth a human's attention), 2 on input error, 3 on version mismatch.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from _common import FINDINGS, INPUT_ERROR, OK, arg_value, emit, run_guarded

# (kind, confidence, pattern). Patterns are ordered; a line may match several kinds. Case-insensitive.
_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "model_call",
        "high",
        r"chat\.completions\.create|messages\.create|generate_content|invoke_model|responses\.create",
    ),
    (
        "model_call",
        "medium",
        r"ChatOpenAI|ChatAnthropic|ChatVertexAI|llm\.(invoke|generate)|\.ainvoke\(",
    ),
    ("tool_call", "high", r"execute_tool|session\.call_tool|tools/call|call_tool\("),
    ("tool_call", "medium", r"@tool\b|@function_tool\b|FunctionTool|ToolNode|Tool\("),
    ("authz", "high", r"openfga|\bopa\b|cedar|check_access|is_allowed|authorize\("),
    ("authz", "medium", r"policy_check|permission|rbac"),
    ("credential", "high", r"token_exchange|oauth|get_secret|secret_manager"),
    ("credential", "medium", r"os\.environ|getenv|api_key|API_KEY"),
    (
        "approval",
        "high",
        r"require_approval|human_in_the_loop|await_approval|HumanApproval",
    ),
    ("approval", "medium", r"\binterrupt\(|\binput\("),
    ("memory", "high", r"memory\.(read|write)|save_memory|MemorySaver|checkpoint"),
    ("retrieval", "high", r"similarity_search|vector_store|retriever|knowledge_base"),
    ("retrieval", "medium", r"retriev|embed\("),
    (
        "component_load",
        "medium",
        r"load_skill|MCPServer|mcp\.connect|load_prompt|from_config|load_catalog",
    ),
    ("decision", "high", r"credit_decision|approve_loan|def decide|status_change"),
    ("decision", "medium", r"recommend|classif"),
    ("incident", "high", r"incident|pagerduty|escalate\("),
    ("incident", "medium", r"\balert\(|\bnotify\("),
    ("instruction_entry", "high", r"system_prompt|SystemMessage|system_message"),
    ("instruction_entry", "medium", r"user_message|instruction"),
    ("refusal", "high", r"refuse|PolicyError|guardrail"),
    ("refusal", "medium", r"\breject\b|\bdeny\b|PermissionError"),
    ("disclosure", "high", r"ai_notice|synthetic_marking|deepfake_label|disclosure"),
)

# Kinds for which an available enforcement point (a gateway/policy/IdP client) matters (SPEC 13.1 STEP 2).
_ENFORCEMENT_KINDS = frozenset({"tool_call", "authz", "credential"})
_ENFORCEMENT_HINT = re.compile(
    r"gateway|openfga|\bopa\b|cedar|policy_engine|admission|MCP_GATEWAY|token_exchange",
    re.IGNORECASE,
)

_COMPILED = tuple(
    (kind, conf, re.compile(pat, re.IGNORECASE)) for kind, conf, pat in _PATTERNS
)


def scan(repo: Path) -> list[dict[str, Any]]:
    """Return the chokepoint inventory for ``repo``, sorted by (path, line, kind)."""
    files = sorted(
        p
        for p in repo.rglob("*.py")
        if ".venv" not in p.parts and "__pycache__" not in p.parts
    )
    enforcement_present = any(
        _ENFORCEMENT_HINT.search(f.read_text(encoding="utf-8", errors="replace"))
        for f in files
    )
    entries: list[dict[str, Any]] = []
    for path in files:
        rel = path.relative_to(repo).as_posix()
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            seen: set[str] = set()
            for kind, confidence, pattern in _COMPILED:
                if kind in seen:
                    continue  # one entry per (line, kind); the first (highest-confidence) pattern wins
                if pattern.search(line):
                    seen.add(kind)
                    entries.append(
                        {
                            "path": rel,
                            "line": lineno,
                            "kind": kind,
                            "confidence": confidence,
                            "enforcement_point_present": enforcement_present
                            and kind in _ENFORCEMENT_KINDS,
                        }
                    )
    entries.sort(key=lambda e: (e["path"], e["line"], e["kind"]))
    return entries


def _body(argv: list[str]) -> int:
    repo_arg = arg_value(argv, "--repo") or "."
    repo = Path(repo_arg)
    if not repo.is_dir():
        emit(
            {"status": "input_error", "message": f"{repo_arg} is not a directory"},
            want_json="--json" in argv,
            human=f"INPUT ERROR: {repo_arg} is not a directory",
        )
        return INPUT_ERROR
    entries = scan(repo)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
    payload = {
        "repo": repo_arg,
        "chokepoints": entries,
        "counts": dict(sorted(counts.items())),
        "total": len(entries),
    }
    human = f"CHOKEPOINTS: {len(entries)} found across {len(counts)} kinds"
    emit(payload, want_json="--json" in argv, human=human)
    return OK if entries else FINDINGS


def main(argv: list[str] | None = None) -> int:
    return run_guarded(list(sys.argv[1:] if argv is None else argv), _body)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
