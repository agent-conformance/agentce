"""Driver for VG-CATALOG-READ-ONCE (18.171, SPEC §8.7, §14.5 CP-3). Not itself a test file: invoked by
catalog_read_once.sh, which builds the TypeScript and Java CLIs first.

Signs a copy of the baseline catalog with the Python CLI, keeps two versions of controls/DOC-01.yaml (the signed
bytes and an edit carrying MARKER), and runs each engine's real CLI with a writer racing it: after the engine's
first read of DOC-01.yaml the file on disk becomes the other version (Python: an audit hook on "open" swaps it just
before a second open; TypeScript: a preload wrapping fs.readFileSync swaps it right after the first read; Java: the
`race-read` seam, a hook the loader calls right after its read). Every case wants exactly one read of
the file, so a run that never read it, or read it twice, fails whatever its exit code. An engine passes when it
assesses, reports and packages only the bytes whose signature it checked:

- no race, signed: assessed (exit 0 or 1), the manifest carries the signed provenance digest;
- no race, edited: refused as input.catalog_unverified (exit 3);
- edit swapped in after the read: assessed, MARKER nowhere in its output, the signed provenance digest;
- signed bytes put back after the read: refused as input.catalog_unverified (exit 3);
- verify --catalog, either way: the digest it reports is the digest of the bytes it read first;
- Python only, assess --package-for-sharing with the edit swapped in: the packaged DOC-01.yaml is the signed bytes;
- Python only, catalog sign with an edit that fails lint swapped in: the signature covers the linted bytes.

    catalog_read_once_check.py [--engines python,typescript,java]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "verification/gates/fixtures/catalog_read_once"
MARKER = "CHANGED-ON-DISK-DURING-THE-RUN-18171"
ENGINES = ("python", "typescript", "java")
SKIPPED = {"__pycache__"}


def tree_digest(folder: Path, exclude: set[str]) -> str:
    """digest_tree as the engines compute it: sha256 over the sorted ``rel\\0sha256(bytes)`` lines."""
    lines = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if rel in exclude or any(part.startswith(".") or part in SKIPPED for part in rel.split("/")):
            continue
        lines.append(rel.encode() + b"\0" + hashlib.sha256(path.read_bytes()).hexdigest().encode())
    return "sha256:" + hashlib.sha256(b"\n".join(lines)).hexdigest()


def provenance_digest(folder: Path) -> str:
    return tree_digest(folder, {"catalog.sig.json", "catalog.yaml"})


def covered_digest(folder: Path) -> str:
    return tree_digest(folder, {"catalog.sig.json"})


class Runner:
    def __init__(self, engine: str, work: Path) -> None:
        self.engine, self.work = engine, work

    def __call__(self, args: list[str], doc: Path, swap: Path | None) -> tuple[int, str, int]:
        count = self.work / "count"
        count.unlink(missing_ok=True)
        env = dict(os.environ)
        swap_arg = str(swap) if swap else "-"
        if self.engine == "python":
            cmd = [sys.executable, str(FIXTURES / "race_read.py"), str(doc), swap_arg, str(count), *args]
        elif self.engine == "typescript":
            cmd = ["node", "-r", str(FIXTURES / "race_read.cjs"), str(ROOT / "engines/typescript/dist/cli.js"), *args]
            env.update(RACE_FILE=str(doc), RACE_SWAP=swap_arg, RACE_COUNT=str(count))
        else:
            jars = sorted((ROOT / "engines/java/build/libs").glob("agentce-*-all.jar"))
            cmd = ["java", "-cp", str(jars[-1]), "org.agentce.Seams", "race-read", str(doc), swap_arg, str(count), *args]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env, timeout=300)
        reads = int(count.read_text()) if count.is_file() else 0
        return p.returncode, p.stdout + p.stderr, reads


def out_text(out: Path) -> str:
    if not out.is_dir():
        return ""
    return "".join(f.read_text(errors="ignore") for f in out.rglob("*") if f.is_file() and f.suffix != ".sqlite")


def manifest_digest(out: Path) -> str | None:
    try:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return next((c.get("digest") for c in manifest["inputs"].get("catalogs", []) if c.get("id") == "baseline"), None)


def check_engine(engine: str, work: Path, cat: Path, signed: bytes, edited: bytes) -> list[str]:
    run = Runner(engine, work)
    doc = cat / "controls" / "DOC-01.yaml"
    out = work / "out"
    (work / "signed.yaml").write_bytes(signed)
    (work / "edited.yaml").write_bytes(edited)
    doc.write_bytes(signed)
    signed_prov, signed_cov = provenance_digest(cat), covered_digest(cat)
    doc.write_bytes(edited)
    edited_cov = covered_digest(cat)
    problems: list[str] = []

    def case(name: str, start: bytes, swap: str | None, args: list[str], judge: Callable[[int, str], str | None]) -> None:
        shutil.rmtree(out, ignore_errors=True)
        doc.write_bytes(start)
        code, text, reads = run(args, doc, work / swap if swap else None)
        problem = f"read DOC-01.yaml {reads} times, not once" if reads != 1 else judge(code, text)
        print(f"{engine}: {'ok  ' if problem is None else 'FAIL'} {name}" + (f" · {problem}" if problem else ""))
        if problem:
            problems.append(f"{engine} {name}: {problem} (exit {code}; {text.strip()[-300:]})")

    assess = ["assess", "--bundle", "corpus/quickstart/evidence", "--profile", "corpus/quickstart/applicability.yaml",
              "--catalog-dir", str(cat), "--trust-root", str(work / "tr.json"), "--out", str(out), "--json"]

    def assessed(code: int, text: str) -> str | None:
        if code not in (0, 1):
            return "not assessed"
        if MARKER in text + out_text(out):
            return "the bytes changed after the read reached the output"
        digest = manifest_digest(out)
        return None if digest == signed_prov else f"manifest digest {digest}, want the signed {signed_prov}"

    def refused(code: int, text: str) -> str | None:
        return None if code == 3 and "input.catalog_unverified" in text else "not refused as input.catalog_unverified"

    def reports(want: str) -> Callable[[int, str], str | None]:
        def judge(code: int, text: str) -> str | None:
            try:
                got = json.JSONDecoder().raw_decode(text, text.index("{\n"))[0]["digest"]
            except (ValueError, KeyError):
                return "no digest in the JSON output"
            return None if got == want else f"reported digest {got}, want {want} (the bytes it read)"

        return judge

    case("no race, signed: assessed", signed, None, assess, assessed)
    case("no race, edited: refused", edited, None, assess, refused)
    case("edit swapped in after the read: assessed from the signed bytes", signed, "edited.yaml", assess, assessed)
    case("signed bytes put back after the read: refused", edited, "signed.yaml", assess, refused)
    verify = ["verify", "--catalog", str(cat), "--json"]
    case("verify --catalog, edit swapped in: the signed digest", signed, "edited.yaml", verify, reports(signed_cov))
    case("verify --catalog, signed put back: the edited digest", edited, "signed.yaml", verify, reports(edited_cov))
    if engine == "python":

        def packaged(code: int, text: str) -> str | None:
            copies = list(out.rglob("DOC-01.yaml"))
            if code not in (0, 1) or not copies:
                return "nothing packaged"
            return None if all(p.read_bytes() == signed for p in copies) else "the packaged DOC-01.yaml is not the signed bytes"

        case("--package-for-sharing, edit swapped in: packaged the signed bytes", signed, "edited.yaml",
             [*assess, "--package-for-sharing"], packaged)
        problems += check_sign(run, work, cat, signed)
    return problems


def check_sign(run: Runner, work: Path, cat: Path, linted: bytes) -> list[str]:
    """Python's `catalog sign` with an edit that fails lint swapped in after the read: what is signed is
    the bytes that were linted, so the signature's digest is theirs, never the edit's."""
    copy = work / "s"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(cat, copy)
    doc = copy / "controls" / "DOC-01.yaml"
    doc.write_bytes(linted)
    want = covered_digest(copy)
    (work / "unlinted.yaml").write_bytes(linted + b"unknown_field_18171: 1\n")
    key = work / "sign-k.pem"
    key.unlink(missing_ok=True)
    args = ["catalog", "sign", str(copy), "--new-key", str(key), "--write-trust-root", str(work / "sign-tr.json"),
            "--force", "--json"]
    code, text, reads = run(args, doc, work / "unlinted.yaml")
    problem = None
    if reads != 1:
        problem = f"read DOC-01.yaml {reads} times, not once"
    elif code != 0:
        problem = "not signed"
    else:
        envelope = json.loads((copy / "catalog.sig.json").read_text(encoding="utf-8"))
        statement = json.loads(base64.b64decode(envelope["payload"]))
        got = "sha256:" + statement["subject"][0]["digest"]["sha256"]
        if got != want:
            problem = f"signed digest {got}, want the linted {want}"
    name = "catalog sign, an edit failing lint swapped in: signed the linted bytes"
    print(f"python: {'ok  ' if problem is None else 'FAIL'} {name}" + (f" · {problem}" if problem else ""))
    return [] if problem is None else [f"python {name}: {problem} (exit {code}; {text.strip()[-300:]})"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default=",".join(ENGINES))
    engines = ap.parse_args().engines.split(",")
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as t:
        work = Path(t)
        cat = work / "c"
        shutil.copytree(ROOT / "spec/catalogs/base/baseline", cat)
        sign = [sys.executable, "-c", "import sys; from agentce import cli; sys.exit(cli.main(sys.argv[1:]))", "catalog", "sign", str(cat), "--new-key", str(work / "k.pem"),
                "--write-trust-root", str(work / "tr.json"), "--force"]
        p = subprocess.run(sign, cwd=ROOT, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"FAIL · signing the catalog copy failed (exit {p.returncode}): {p.stdout[-300:]}{p.stderr[-300:]}")
            return 1
        doc = cat / "controls" / "DOC-01.yaml"
        signed = doc.read_bytes()
        edited = signed.replace(b"Operating components match", MARKER.encode(), 1)
        if edited == signed:
            print("FAIL · DOC-01.yaml no longer holds the text this gate edits")
            return 1
        for engine in engines:
            problems += check_engine(engine, work, cat, signed, edited)
    for problem in problems:
        print(f"FAIL · {problem}")
    print("VG-CATALOG-READ-ONCE: " + ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
