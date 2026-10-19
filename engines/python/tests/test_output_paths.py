"""Every output a command writes is checked through one role and refused under its key: a target of
the wrong type, one that cannot be reached, or a folder that cannot be written answers exit 3 before
anything is written, and a FIFO is never waited on, as the argument or at a fixed name inside the
folder the command writes (SPEC §8.7, §8.5; item 18.180)."""

from __future__ import annotations

import json
import os
import shutil
import signal
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from agentce import cli
from agentce.errors import InputError
from agentce.outputs import (
    DANGLING,
    NOT_A_DIRECTORY,
    NOT_A_FILE,
    UNREACHABLE,
    UNWRITABLE,
    OutputRole,
    check_output,
    write_file,
    write_output,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"
_CONFIG = str(_REPO_ROOT / "adapters" / "fixtures" / "collect-dry-run.yaml")
_FILE = OutputRole("input.out_file_unwritable", "--out", "pass a writable file.")
_DIR = OutputRole("input.out_dir_unwritable", "--out", "pass a writable folder.", True)
_ROOT = os.geteuid() == 0


@pytest.fixture(autouse=True)
def _never_hang() -> Iterator[None]:
    """A FIFO open that blocks fails the test after ten seconds instead of hanging the suite."""

    def _hung(*_: object) -> None:
        raise TimeoutError("an output write blocked")

    previous = signal.signal(signal.SIGALRM, _hung)
    signal.alarm(10)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _refusal(raw: str, role: OutputRole) -> InputError:
    with pytest.raises(InputError) as caught:
        check_output(raw, role)
    assert caught.value.key == role.key
    return caught.value


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    code = cli.main([argv[0], "--json", *argv[1:]])
    return code, json.loads(capsys.readouterr().out)


def test_a_folder_given_as_a_file_is_not_a_regular_file(tmp_path: Path) -> None:
    assert NOT_A_FILE in _refusal(str(tmp_path), _FILE).cause


def test_a_file_given_as_a_folder_is_not_a_directory(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x", encoding="utf-8")
    assert NOT_A_DIRECTORY in _refusal(str(tmp_path / "f"), _DIR).cause


def test_a_fifo_is_refused_without_being_opened(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "fifo")
    assert NOT_A_FILE in _refusal(str(tmp_path / "fifo"), _FILE).cause
    with pytest.raises(InputError) as caught:
        write_output(str(tmp_path / "fifo"), _FILE, "x")
    assert caught.value.key == _FILE.key


def test_write_file_fails_on_a_fifo_with_no_reader_instead_of_waiting(
    tmp_path: Path,
) -> None:
    """The check-free writer used inside a folder: a FIFO swapped in at a fixed name fails the
    non-blocking open (or the descriptor check), it never blocks."""
    os.mkfifo(tmp_path / "fifo")
    with pytest.raises(OSError):
        write_file(tmp_path / "fifo", "x")


def test_a_dangling_link_is_named(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to(tmp_path / "gone")
    assert DANGLING in _refusal(str(tmp_path / "link"), _FILE).cause


@pytest.mark.parametrize("raw", ["loop", "loop/x", "n" * 300, "n" * 300 + "/x"])
def test_a_loop_or_a_long_name_cannot_be_reached(tmp_path: Path, raw: str) -> None:
    (tmp_path / "loop").symlink_to("loop")
    assert UNREACHABLE in _refusal(str(tmp_path / raw), _FILE).cause


@pytest.mark.skipif(_ROOT, reason="root writes into a read-only folder")
def test_a_read_only_folder_cannot_be_written(tmp_path: Path) -> None:
    folder = tmp_path / "ro"
    folder.mkdir(mode=0o555)
    try:
        assert UNWRITABLE in _refusal(str(folder / "x.md"), _FILE).cause
        assert UNWRITABLE in _refusal(str(folder), _DIR).cause
    finally:
        folder.chmod(0o755)


@pytest.mark.skipif(_ROOT, reason="root enters a mode-000 folder")
def test_a_folder_that_cannot_be_entered_cannot_be_reached(tmp_path: Path) -> None:
    folder = tmp_path / "shut"
    folder.mkdir(mode=0o000)
    try:
        assert UNREACHABLE in _refusal(str(folder / "x.md"), _FILE).cause
    finally:
        folder.chmod(0o755)


def test_a_new_file_in_new_folders_is_written(tmp_path: Path) -> None:
    path = write_output(str(tmp_path / "a" / "b" / "r.md"), _FILE, "report")
    assert path.read_text(encoding="utf-8") == "report"


@pytest.mark.parametrize(
    ("argv", "fifo", "key"),
    [
        (
            ["collect", "--config", _CONFIG, "--dry-run", "--out", "{t}/b"],
            "b/manifest.json",
            "input.out_dir_unwritable",
        ),
        (
            ["init", "--subject", "s", "--force", "--out", "{t}/p"],
            "p/agentce/applicability.yaml",
            "input.out_dir_unwritable",
        ),
        (
            ["catalog", "init", "{t}/c", "--force"],
            "c/catalog.yaml",
            "catalog.init_dir_unwritable",
        ),
    ],
)
def test_a_fifo_at_a_fixed_name_inside_the_output_folder_is_refused(
    argv: list[str],
    fifo: str,
    key: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / fifo).parent.mkdir(parents=True)
    os.mkfifo(tmp_path / fifo)
    code, env = _run([a.replace("{t}", str(tmp_path)) for a in argv], capsys)
    assert (code, env["error"]["key"]) == (3, key)


@pytest.fixture(scope="module")
def _assessed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("assessed") / "report"
    code = cli.main(
        [
            "assess",
            "--bundle",
            str(_QUICKSTART / "evidence"),
            "--profile",
            str(_QUICKSTART / "applicability.yaml"),
            "--domain",
            str(_QUICKSTART / "domain.linkml.yaml"),
            "--out",
            str(out),
        ]
    )
    assert code in (0, 1)
    return out


def _report(assessed: Path, tmp_path: Path) -> Path:
    report = tmp_path / "report"
    shutil.copytree(assessed, report)
    return report


def _key(tmp_path: Path) -> str:
    pem = Ed25519PrivateKey.generate().private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    (tmp_path / "k.pem").write_bytes(pem)
    return str(tmp_path / "k.pem")


def _sign(
    report: Path, key: str, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict]:
    return _run(
        ["sign", str(report), "--as", "claimant", "--profile", "kms", "--key", key],
        capsys,
    )


def _snapshot(report: Path) -> dict[str, Any]:
    return {
        str(p.relative_to(report)): p.read_bytes()
        for p in sorted(report.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


@pytest.mark.parametrize("kind", ["fifo", "folder", "signatures_file"])
def test_sign_refuses_a_bad_write_target_before_rewriting_the_claim(
    _assessed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    report = _report(_assessed, tmp_path)
    shutil.rmtree(report / "signatures", ignore_errors=True)
    if kind == "signatures_file":
        (report / "signatures").write_text("x", encoding="utf-8")
    else:
        (report / "signatures").mkdir()
        target = report / "signatures" / "claimant-kms.dsse.json"
        if kind == "fifo":
            os.mkfifo(target)
        else:
            target.mkdir()
    before = _snapshot(report)
    code, env = _sign(report, _key(tmp_path), capsys)
    assert (code, env["error"]["key"]) == (3, "input.report_dir_unwritable")
    assert _snapshot(report) == before


def test_sign_still_signs_a_good_report(
    _assessed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(_assessed, tmp_path)
    code, env = _sign(report, _key(tmp_path), capsys)
    assert code == 0, env
    assert (report / "signatures" / "claimant-kms.dsse.json").is_file()


@pytest.mark.skipif(_ROOT, reason="root writes into a read-only folder")
def test_readiness_on_a_read_only_report_warns_under_its_key(
    _assessed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(_assessed, tmp_path)
    report.chmod(0o555)
    try:
        code = cli.main(["readiness", str(report)])
        out = capsys.readouterr().out
        json_code, env = _run(["readiness", str(report)], capsys)
    finally:
        report.chmod(0o755)
    assert code == json_code
    assert code in (0, 1)
    assert "warning [warning.readiness_file_unwritten]:" in out
    assert "The verdict above is not saved to a file." in out
    assert env["report"] is None
    assert env["report_warning"] == "warning.readiness_file_unwritten"


@pytest.mark.parametrize("force", [[], ["--force"]])
def test_catalog_sign_refuses_a_fifo_at_its_signature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], force: list[str]
) -> None:
    catalog = tmp_path / "cat"
    assert cli.main(["catalog", "init", str(catalog)]) == 0
    capsys.readouterr()
    os.mkfifo(catalog / "catalog.sig.json")
    code, env = _run(
        ["catalog", "sign", str(catalog), "--key", _key(tmp_path), *force], capsys
    )
    assert (code, env["error"]["key"]) == (3, "catalog.sign_dir_unwritable")
