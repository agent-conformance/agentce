#!/usr/bin/env python3
"""trace_store_ingest_check - prove trace-store telemetry round-trips into AgentCE, for real.

Item 16.3 (trace-store ingestion) adds no new adapter code: Langfuse, Phoenix/Arize, Datadog LLM
Observability, and LangSmith all already emit OTel GenAI-semconv or OpenInference-convention spans --
the two conventions ``agentce_adapters.otel_genai`` already maps (SPEC 12), convention-based rather
than vendor-based. This tool proves two independent facts about that claim, neither standing in for
the other:

**C3 -- the round-trip proof (the default invocation).** For each of the four vendor fixtures under
``adapters/otel-genai/fixtures/{langfuse,phoenix,datadog,langsmith}``, a real ``agentce ingest`` turns
the committed native export into a bundle ``agentce validate`` accepts with zero quarantines --
reusing ``tools/collect_ingest_check.check_ingest`` directly, never reimplementing ingestion. It also
runs the adapter's own ``check_support_matrix`` (``adapters/otel-genai/src/agentce_adapters/
check_support_matrix.py``), which independently proves every fixture's ``expected.jsonl`` is
byte-identical to what ``adapt()`` really produces and that ``support-matrix.yaml`` stays consistent
with the conventions and members the fixtures (old and new) actually exercise -- so a fixture cannot
silently introduce a convention or member the matrix does not declare.

**C4 -- the Collector recipe proof (``--recipes-only``).** Each of the four Collector recipes under
``adapters/otel-genai/otel-collector/recipes/`` is structurally validated: it parses as YAML; it
declares ``receivers``/``processors``/``exporters``/``service`` top level; its ``otlp`` receiver is
byte-identical (the same parsed structure) to the shared one in
``adapters/otel-genai/otel-collector/config.yaml``; its named vendor exporter's type and endpoint
match ``VENDOR_EXPORTERS`` below (built from the vendor research recorded in each recipe's own header
comment); a ``file`` exporter sits in the same traces pipeline (the practical fan-out to
``agentce ingest``); and the header comment cites both a source URL and a retrieval date.

    trace_store_ingest_check.py                 run C3 (the round-trip proof); the invocation CI uses
    trace_store_ingest_check.py --recipes-only  run C4 (the Collector recipe proof) instead
    trace_store_ingest_check.py --self-test     prove C3 discriminates: a fixture whose expected.jsonl
                                                 is deliberately wrong is caught, against a synthetic
                                                 tmp fixture, never a committed one
    trace_store_ingest_check.py --recipes-only --self-test
                                                 prove C4 discriminates: a missing/drifted otlp
                                                 receiver, a missing file exporter, a mismatched vendor
                                                 exporter endpoint, and a missing source citation are
                                                 each caught, against synthetic tmp recipes

Run in the engine's environment, as ``tools/collect_ingest_check.py`` documents:
``uv run --project engines/python --frozen python tools/trace_store_ingest_check.py``. PyYAML (already
a dependency of ``engines/python`` and of ``agentce-tools``) parses the recipes; otherwise standard
library only. No network, no learned component.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
ADAPTER_ROOT = ROOT / "adapters" / "otel-genai"
SHARED_CONFIG = ADAPTER_ROOT / "otel-collector" / "config.yaml"

VENDORS = ("langfuse", "phoenix", "datadog", "langsmith")

# The vendor's own exporter -- name and endpoint -- each recipe under otel-collector/recipes/ must
# declare, per the vendor research recorded in that recipe's own header comment (item 16.3 brief).
VENDOR_EXPORTERS: dict[str, tuple[str, str]] = {
    "langfuse": ("otlphttp/langfuse", "https://cloud.langfuse.com/api/public/otel"),
    "phoenix": ("otlphttp/phoenix", "${PHOENIX_COLLECTOR_ENDPOINT}"),
    "datadog": ("otlphttp/datadog", "http://localhost:4318"),
    "langsmith": ("otlphttp/langsmith", "https://api.smith.langchain.com/otel"),
}

REQUIRED_TOP_KEYS = {"receivers", "processors", "exporters", "service"}
URL_RE = re.compile(r"https?://\S+")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

sys.path.insert(0, str(ADAPTER_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect_ingest_check import check_ingest  # noqa: E402  (needs the sys.path insert above)


def _check_support_matrix(adapter_root: Path) -> list[str]:
    """Import and run the adapter's own ``check_support_matrix.check`` (never reimplemented here)."""
    from agentce_adapters.check_support_matrix import (  # type: ignore[import-not-found]
        check as check_matrix,
    )

    return check_matrix(adapter_root)


# --- C3: the round-trip proof. ---------------------------------------------------------------------


def check_c3(root: Path = ROOT) -> list[str]:
    """Every vendor fixture ingests and validates cleanly, and the support matrix stays consistent."""
    problems: list[str] = []
    for vendor in VENDORS:
        fixture = f"adapters/otel-genai/fixtures/{vendor}/input.json"
        error = check_ingest(root, fixture=fixture)
        if error is not None:
            problems.append(f"{vendor}: {error}")
    matrix_root = root / "adapters" / "otel-genai"
    for failure in _check_support_matrix(matrix_root):
        problems.append(f"support-matrix: {failure}")
    return problems


# --- C4: the Collector recipe proof. ----------------------------------------------------------------


def _leading_comment(text: str) -> str:
    """The header comment block: every ``#``-prefixed or blank line before the first real YAML line."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            lines.append(line)
        elif stripped == "":
            continue
        else:
            break
    return "\n".join(lines)


def _shared_otlp_receiver(config_path: Path = SHARED_CONFIG) -> Any:
    doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{config_path} is not a YAML mapping")
    receivers = doc.get("receivers")
    if not isinstance(receivers, dict) or "otlp" not in receivers:
        raise ValueError(f"{config_path} declares no receivers.otlp")
    return receivers["otlp"]


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def validate_recipe(vendor: str, path: Path, shared_otlp: Any) -> list[str]:
    """Structurally validate one Collector recipe; return the (possibly empty) list of problems."""
    if not path.is_file():
        return [f"{path}: missing"]
    text = path.read_text(encoding="utf-8")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"{path}: not valid YAML: {exc}"]
    if not isinstance(doc, dict):
        return [f"{path}: top-level YAML is not a mapping"]

    problems: list[str] = []
    missing_keys = REQUIRED_TOP_KEYS - doc.keys()
    if missing_keys:
        problems.append(f"{path}: missing top-level key(s) {sorted(missing_keys)}")

    receivers = _as_dict(doc.get("receivers"))
    otlp = receivers.get("otlp")
    if otlp != shared_otlp:
        problems.append(
            f"{path}: receivers.otlp is not byte-identical to {SHARED_CONFIG.relative_to(ROOT)}'s"
        )

    exporters = _as_dict(doc.get("exporters"))
    if "file" not in exporters:
        problems.append(f"{path}: no `file` exporter is declared")

    if vendor not in VENDOR_EXPORTERS:
        problems.append(f"{path}: {vendor!r} is not in VENDOR_EXPORTERS")
    else:
        exporter_name, expected_endpoint = VENDOR_EXPORTERS[vendor]
        vendor_cfg = _as_dict(exporters.get(exporter_name))
        got_endpoint = vendor_cfg.get("endpoint")
        if got_endpoint != expected_endpoint:
            problems.append(
                f"{path}: {exporter_name} endpoint is {got_endpoint!r}, expected {expected_endpoint!r}"
            )

        service = _as_dict(doc.get("service"))
        pipelines = _as_dict(service.get("pipelines"))
        traces = _as_dict(pipelines.get("traces"))
        pipeline_exporters = _as_list(traces.get("exporters"))
        if "file" not in pipeline_exporters:
            problems.append(
                f"{path}: `file` exporter is not wired into service.pipelines.traces"
            )
        if exporter_name not in pipeline_exporters:
            problems.append(
                f"{path}: {exporter_name} is not wired into service.pipelines.traces"
            )

    header = _leading_comment(text)
    if not URL_RE.search(header):
        problems.append(f"{path}: header comment cites no source URL")
    if not DATE_RE.search(header):
        problems.append(f"{path}: header comment cites no retrieval date")

    return problems


def check_c4(root: Path = ROOT) -> list[str]:
    """Every vendor recipe is structurally sound and consistent with the shared receiver and table."""
    recipes_dir = root / "adapters" / "otel-genai" / "otel-collector" / "recipes"
    shared_config = root / "adapters" / "otel-genai" / "otel-collector" / "config.yaml"
    try:
        shared_otlp = _shared_otlp_receiver(shared_config)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    problems: list[str] = []
    for vendor in VENDORS:
        problems.extend(
            validate_recipe(vendor, recipes_dir / f"{vendor}.yaml", shared_otlp)
        )
    return problems


# --- self-test ----------------------------------------------------------------------------------------


def _selftest_c3() -> bool:
    ok = True
    problems = check_c3(ROOT)
    if problems:
        print(f"self-test c3-good-tree: FAIL {problems}")
        ok = False
    else:
        print("self-test c3-good-tree: ok")

    # A fixture whose expected.jsonl is deliberately mismatched from what adapt() really produces is
    # caught -- against a synthetic tmp fixture built from the real langfuse fixture's own input, never
    # a committed fixture (the committed ones must stay correct).
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        fixture_dir = tmp_root / "fixtures" / "broken"
        fixture_dir.mkdir(parents=True)
        src = ADAPTER_ROOT / "fixtures" / "langfuse"
        (fixture_dir / "input.json").write_text(
            (src / "input.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (fixture_dir / "adapt.json").write_text(
            (src / "adapt.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        lines = (src / "expected.jsonl").read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["subject"] = (
            "spiffe://corp/agents/deliberately-wrong"  # a real field, mutated
        )
        lines[0] = json.dumps(first)
        (fixture_dir / "expected.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        (tmp_root / "support-matrix.yaml").write_text(
            "adapter: otel-genai\nconventions: []\nevents: {}\n", encoding="utf-8"
        )
        failures = _check_support_matrix(tmp_root)
        if any("does not match expected.jsonl" in f for f in failures):
            print("self-test c3-mismatched-expected-is-caught: ok")
        else:
            print(f"self-test c3-mismatched-expected-is-caught: FAIL {failures}")
            ok = False
    return ok


def _shared_otlp_text() -> dict[str, Any]:
    return {
        "protocols": {
            "grpc": {"endpoint": "0.0.0.0:4317"},
            "http": {"endpoint": "0.0.0.0:4318"},
        }
    }


def _good_doc(vendor: str) -> dict[str, Any]:
    exporter_name, endpoint = VENDOR_EXPORTERS[vendor]
    return {
        "receivers": {"otlp": _shared_otlp_text()},
        "processors": {"batch": {}},
        "exporters": {
            exporter_name: {"endpoint": endpoint},
            "file": {"path": "/tmp/traces.json", "format": "json"},
        },
        "service": {
            "pipelines": {
                "traces": {
                    "receivers": ["otlp"],
                    "processors": ["batch"],
                    "exporters": [exporter_name, "file"],
                }
            }
        },
    }


_GOOD_HEADER = "# See https://example.invalid/docs/otel (retrieved 2026-09-24).\n"


def _write_recipe(path: Path, doc: dict[str, Any], header: str = _GOOD_HEADER) -> None:
    path.write_text(header + yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _selftest_c4() -> bool:
    ok = True
    problems = check_c4(ROOT)
    if problems:
        print(f"self-test c4-good-tree: FAIL {problems}")
        ok = False
    else:
        print("self-test c4-good-tree: ok")

    vendor = "langfuse"
    shared_otlp = _shared_otlp_text()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)

        good = tmp_root / "good.yaml"
        _write_recipe(good, _good_doc(vendor))
        if validate_recipe(vendor, good, shared_otlp):
            print(
                f"self-test c4-synthetic-good-recipe: FAIL {validate_recipe(vendor, good, shared_otlp)}"
            )
            ok = False
        else:
            print("self-test c4-synthetic-good-recipe: ok")

        drifted = tmp_root / "drifted-otlp.yaml"
        doc = _good_doc(vendor)
        doc["receivers"]["otlp"]["protocols"]["http"]["endpoint"] = "0.0.0.0:9999"
        _write_recipe(drifted, doc)
        found = validate_recipe(vendor, drifted, shared_otlp)
        if any("not byte-identical" in p for p in found):
            print("self-test c4-drifted-otlp-receiver-is-caught: ok")
        else:
            print(f"self-test c4-drifted-otlp-receiver-is-caught: FAIL {found}")
            ok = False

        missing_receiver = tmp_root / "missing-otlp.yaml"
        doc = _good_doc(vendor)
        del doc["receivers"]["otlp"]
        _write_recipe(missing_receiver, doc)
        found = validate_recipe(vendor, missing_receiver, shared_otlp)
        if any("not byte-identical" in p for p in found):
            print("self-test c4-missing-otlp-receiver-is-caught: ok")
        else:
            print(f"self-test c4-missing-otlp-receiver-is-caught: FAIL {found}")
            ok = False

        no_file = tmp_root / "no-file-exporter.yaml"
        doc = _good_doc(vendor)
        del doc["exporters"]["file"]
        doc["service"]["pipelines"]["traces"]["exporters"] = [
            VENDOR_EXPORTERS[vendor][0]
        ]
        _write_recipe(no_file, doc)
        found = validate_recipe(vendor, no_file, shared_otlp)
        if any("no `file` exporter" in p for p in found):
            print("self-test c4-no-file-exporter-is-caught: ok")
        else:
            print(f"self-test c4-no-file-exporter-is-caught: FAIL {found}")
            ok = False

        bad_endpoint = tmp_root / "bad-endpoint.yaml"
        doc = _good_doc(vendor)
        doc["exporters"][VENDOR_EXPORTERS[vendor][0]]["endpoint"] = (
            "https://example.invalid/wrong"
        )
        _write_recipe(bad_endpoint, doc)
        found = validate_recipe(vendor, bad_endpoint, shared_otlp)
        if any("endpoint is" in p for p in found):
            print("self-test c4-mismatched-vendor-endpoint-is-caught: ok")
        else:
            print(f"self-test c4-mismatched-vendor-endpoint-is-caught: FAIL {found}")
            ok = False

        no_citation = tmp_root / "no-citation.yaml"
        _write_recipe(
            no_citation,
            _good_doc(vendor),
            header="# just a comment, nothing else here\n",
        )
        found = validate_recipe(vendor, no_citation, shared_otlp)
        if any("no source URL" in p for p in found) and any(
            "no retrieval date" in p for p in found
        ):
            print("self-test c4-missing-citation-is-caught: ok")
        else:
            print(f"self-test c4-missing-citation-is-caught: FAIL {found}")
            ok = False

    return ok


def self_test(recipes_only: bool) -> int:
    ok = _selftest_c4() if recipes_only else _selftest_c3()
    label = "C4 (recipes)" if recipes_only else "C3 (round-trip)"
    print(f"trace_store_ingest_check self-test [{label}]: {'ok' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    recipes_only = "--recipes-only" in args
    if "--self-test" in args:
        return self_test(recipes_only)
    problems = check_c4(ROOT) if recipes_only else check_c3(ROOT)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    label = "C4 (Collector recipes)" if recipes_only else "C3 (round-trip)"
    print(f"trace_store_ingest_check: ok — {label} verified for {', '.join(VENDORS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
