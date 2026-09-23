"""Adapt one real export file through one adapter, in that adapter's own environment.

Invoked by the engine's ``agentce ingest`` and ``agentce collect`` (a source with a local ``export``)
as ``uv run --project <adapter_dir> python _ingest.py <adapter_dir> <export_file> --subject ...
[--source-class ...] [--source ...] [--engine ...]`` -- mirrors ``_probe.py``'s own per-adapter
subprocess pattern, required because every v1 adapter package shares the name ``agentce_adapters`` and
so cannot be imported alongside another. Prints one JSON line: ``{"events": [...]}`` on success, or
``{"error": "..."}`` (exit 1) when the export does not match the adapter's expected shape.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

#: adapter directory name -> its ``agentce_adapters`` submodule. Each module exposes
#: ``adapt(payload, *, subject, source_class=..., source=None) -> AdaptResult``; ``policy-engines``
#: additionally requires ``engine``.
_MODULES = {
    "otel-genai": "agentce_adapters.otel_genai",
    "mcp-gateway": "agentce_adapters.mcp_gateway",
    "policy-engines": "agentce_adapters.policy_engines",
    "oversight": "agentce_adapters.oversight",
    "supply-chain": "agentce_adapters.supply_chain",
    "identity": "agentce_adapters.identity",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("adapter_dir")
    parser.add_argument("export_file")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--source-class", dest="source_class", default="self_report")
    parser.add_argument("--source")
    parser.add_argument("--engine")
    ns = parser.parse_args(argv)

    adapter_name = Path(ns.adapter_dir).name
    module_name = _MODULES.get(adapter_name)
    if module_name is None:
        print(json.dumps({"error": f"unknown adapter {adapter_name!r}"}))
        return 1

    module = importlib.import_module(module_name)
    payload = Path(ns.export_file).read_bytes()
    kwargs: dict[str, object] = {
        "subject": ns.subject,
        "source_class": ns.source_class,
    }
    if ns.source:
        kwargs["source"] = ns.source
    if ns.engine:
        kwargs["engine"] = ns.engine
    try:
        result = module.adapt(payload, **kwargs)
    except module.AdapterError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    except TypeError as exc:
        print(json.dumps({"error": f"{adapter_name} adapt() rejected its arguments: {exc}"}))
        return 1
    except RecursionError:
        print(
            json.dumps(
                {"error": f"the export is nested too deeply for {adapter_name} to parse safely"}
            )
        )
        return 1
    print(json.dumps({"events": result.events}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
