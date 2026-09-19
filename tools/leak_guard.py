#!/usr/bin/env python3
"""leak_guard - server-side private-material leakage guard.

Enforces in CI, on every push and pull request, the same private-material invariant the maintainer's
local git hooks enforce: no private term in added file content or in a commit message, and no
commit-denylisted term in a commit message. It does so WITHOUT the private lists ever entering this
public repository -- it matches against salted SHA-256 digests of the normalized terms
(``tools/leak_tokens.hashes``), and on a hit prints only a stable message key and a ``file:line``
location, never the matched text.

Coverage and limits (see ``docs/adr/0013-server-side-leakage-guard.md``):

- Catches token- and phrase-exact occurrences. A term is normalized to lowercase, its maximal
  ``[a-z0-9]+`` runs joined by single spaces; the scanner hashes every n-gram of the text up to the
  ``ngram_max`` recorded in the digest file and flags any digest match. This is the common
  accidental-leak case (a private name written as a word or short phrase).
- A one-way digest cannot do substring or regular-expression matching, so a term embedded inside a
  larger token, or a term written as a regex in the private list, is not caught here; that fidelity is
  provided by the maintainer's local ``pre-commit`` / ``commit-msg`` / ``pre-push`` hooks and by
  pre-merge review. The digest guard is the server-side floor that also covers fork pull requests.
- Needs no secret and no private checkout, so it is safe under the default ``pull_request`` trigger for
  fork pull requests; ``pull_request_target`` is never used.

Uses only the standard library, so CI runs it with a bare Python and no install step. No network, no
learned component.

    leak_guard.py --self-test        prove detection has teeth (a planted sentinel) and clears clean text
    leak_guard.py --diff <BASE>      scan added content and commit messages of <BASE>..HEAD (CI use)
    leak_guard.py --files <path>...  scan whole files as added content (self-scan of the guard artifacts)
    leak_guard.py --stdin            scan standard input as commit-message text
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HASHES = HERE / "leak_tokens.hashes"

# The empty tree: a stable base for "everything is added" when no real base commit is available
# (a brand-new branch whose push event carries the all-zero before-sha).
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Stable message keys (never the matched text). One per (material, location) class.
KEY_CONTENT = "leak.private_content"
KEY_PRIVATE_MSG = "leak.private_message"
KEY_DENYLISTED_MSG = "leak.denylisted_message"


def normalize_tokens(text: str) -> list[str]:
    """The maximal ``[a-z0-9]+`` runs of ``text``, lowercased -- the shared normalization for both the
    committed digests and the scanned candidates."""
    return re.findall(r"[a-z0-9]+", text.lower())


def ngrams(tokens: list[str], nmax: int) -> set[str]:
    """Every contiguous 1..nmax word n-gram of ``tokens``, joined by single spaces."""
    out: set[str] = set()
    for n in range(1, nmax + 1):
        for i in range(0, len(tokens) - n + 1):
            out.add(" ".join(tokens[i : i + n]))
    return out


def digest(salt: bytes, normalized: str) -> str:
    return hashlib.sha256(salt + b"\x1f" + normalized.encode("utf-8")).hexdigest()


class DigestSet:
    """The salted-digest matcher loaded from a ``leak_tokens.hashes`` file."""

    def __init__(
        self, salt: bytes, ngram_max: int, both: set[str], message: set[str]
    ) -> None:
        self.salt = salt
        self.ngram_max = ngram_max
        self.both = both  # private terms: banned in content and in messages
        self.message = message  # commit-denylist terms: banned in messages only

    @classmethod
    def load(cls, path: Path) -> DigestSet:
        salt: bytes | None = None
        ngram_max = 1
        both: set[str] = set()
        message: set[str] = set()
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("salt="):
                salt = bytes.fromhex(line.split("=", 1)[1].strip())
                continue
            if line.startswith("ngram_max="):
                ngram_max = int(line.split("=", 1)[1].strip())
                continue
            scope, _, dg = line.partition(" ")
            dg = dg.strip()
            if scope == "both":
                both.add(dg)
            elif scope == "message":
                message.add(dg)
            else:  # pragma: no cover - guarded by the file-format check in CI
                raise ValueError(f"unrecognized scope in {path.name}: {scope!r}")
        if salt is None:
            raise ValueError(f"{path.name} declares no salt")
        return cls(salt, ngram_max, both, message)

    def _hit(self, text: str, candidates: set[str]) -> bool:
        toks = normalize_tokens(text)
        for gram in ngrams(toks, self.ngram_max):
            if digest(self.salt, gram) in candidates:
                return True
        return False

    def content_hit(self, text: str) -> bool:
        """A private term in file content."""
        return self._hit(text, self.both)

    def message_key(self, text: str) -> str | None:
        """The message key for a commit message, or None. Private terms are reported as private; the
        commit-denylist terms as denylisted. Private is checked first so a term on both lists reports as
        the stronger (private) leak."""
        toks = normalize_tokens(text)
        grams = ngrams(toks, self.ngram_max)
        digs = {digest(self.salt, g) for g in grams}
        if digs & self.both:
            return KEY_PRIVATE_MSG
        if digs & self.message:
            return KEY_DENYLISTED_MSG
        return None


# --- diff parsing -------------------------------------------------------------------------------


def _iter_added_lines(diff_text: str):
    """Yield ``(path, lineno, text)`` for every added line in a unified diff, tracking the destination
    file and new-file line numbers from the ``+++``/``@@`` headers."""
    path = "?"
    new_lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            path = p[2:] if p.startswith("b/") else p
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_lineno = int(m.group(1)) if m else 0
            continue
        if line.startswith("+"):
            yield path, new_lineno, line[1:]
            new_lineno += 1
        elif line.startswith("-"):
            continue
        else:  # context line (only with >0 context; -U0 emits none)
            new_lineno += 1


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def _resolve_base(base: str) -> str:
    """A usable base tree-ish for the diff. Falls back to the previous commit, then to the empty tree,
    so the guard always has a well-defined 'added since' set (a new branch, a squashed history)."""
    if base and base.strip("0"):
        try:
            return (
                _git(["rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"]).strip()
                or base
            )
        except subprocess.CalledProcessError:
            pass
    try:
        return _git(["rev-parse", "--verify", "--quiet", "HEAD~1^{commit}"]).strip()
    except subprocess.CalledProcessError:
        return EMPTY_TREE


def scan_diff(base: str, digests: DigestSet) -> list[str]:
    """Scan the content added between ``base`` and HEAD, and the messages of the commits in that range.
    Returns violation lines (``<key> <location>``), never the matched text."""
    resolved = _resolve_base(base)
    violations: list[str] = []

    diff = _git(["diff", "--no-color", "-U0", resolved, "HEAD"])
    for path, lineno, text in _iter_added_lines(diff):
        if digests.content_hit(text):
            violations.append(f"{KEY_CONTENT} {path}:{lineno}")

    if resolved == EMPTY_TREE:
        log_range = ["HEAD"]
    else:
        log_range = [f"{resolved}..HEAD"]
    # NUL-delimited so a message body never splits a record.
    log = _git(["log", "--format=%H%x00%B%x00", *log_range])
    records = log.split("\x00")
    for i in range(0, len(records) - 1, 2):
        sha = records[i].strip().splitlines()[-1] if records[i].strip() else "?"
        body = records[i + 1]
        key = digests.message_key(body)
        if key:
            violations.append(f"{key} commit:{sha[:12]}")
    return violations


def scan_files(paths: list[str], digests: DigestSet) -> list[str]:
    """Scan whole files as content (the guard's self-scan of its own committed artifacts)."""
    violations: list[str] = []
    for p in paths:
        fp = Path(p)
        try:
            text = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if digests.content_hit(line):
                violations.append(f"{KEY_CONTENT} {p}:{lineno}")
    return violations


def _report(violations: list[str], what: str) -> int:
    if violations:
        print(f"LEAK-GUARD FAILED: private material in {what}:")
        for v in violations:
            print(f"  {v}")
        print(
            "(the offending text is withheld; see the message key and location above)"
        )
        return 1
    print(f"LEAK-GUARD OK ({what}, 0 leaks)")
    return 0


# --- self-test ----------------------------------------------------------------------------------


def self_test() -> int:
    """Prove the matcher has teeth with a harmless made-up sentinel (never a real term): a single-token
    and a three-word sentinel are planted and must be caught, ordinary text must clear, and the report
    must never echo the sentinel."""
    sentinel_word = "zqxleaksentinel"
    sentinel_phrase = "zqx leak phrase"
    salt = bytes.fromhex(
        "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    )
    with tempfile.TemporaryDirectory() as td:
        hp = Path(td) / "sentinel.hashes"
        hp.write_text(
            "# test-only sentinel digest set\n"
            f"salt={salt.hex()}\n"
            "ngram_max=3\n"
            f"both {digest(salt, sentinel_word)}\n"
            f"both {digest(salt, sentinel_phrase)}\n"
            f"message {digest(salt, 'zqx denylist trailer')}\n",
            encoding="utf-8",
        )
        ds = DigestSet.load(hp)

        # (1) the single-token sentinel is caught in content, with a real location and no disclosure.
        planted = Path(td) / "planted.txt"
        planted.write_text(
            f"a line\nthis mentions the {sentinel_word.upper()} here\nok\n",
            encoding="utf-8",
        )
        v = scan_files([str(planted)], ds)
        assert len(v) == 1 and v[0].startswith(f"{KEY_CONTENT} "), v
        assert v[0].endswith(":2"), v
        assert sentinel_word not in " ".join(v), "the report disclosed the token"

        # (2) the three-word sentinel phrase is caught only as an n-gram (ngram_max=3), across punctuation.
        phrased = Path(td) / "phrase.txt"
        phrased.write_text(
            "intro\nthe Zqx-Leak, Phrase! trails here\n", encoding="utf-8"
        )
        vp = scan_files([str(phrased)], ds)
        assert len(vp) == 1 and vp[0].startswith(f"{KEY_CONTENT} "), vp

        # (3) a message-scope-only sentinel is caught in a message but NOT in content.
        assert ds.message_key("chore: zqx denylist trailer") == KEY_DENYLISTED_MSG
        msgfile = Path(td) / "msg.txt"
        msgfile.write_text("chore: zqx denylist trailer\n", encoding="utf-8")
        assert scan_files([str(msgfile)], ds) == [], (
            "message-only term must not fire on content"
        )

        # (4) ordinary content and messages clear.
        clean = Path(td) / "clean.txt"
        clean.write_text(
            "An ordinary contributor line about assessments and catalogs.\n",
            encoding="utf-8",
        )
        assert scan_files([str(clean)], ds) == [], "clean content flagged"
        assert ds.message_key("feat(cli): add a real assessment path") is None
        assert not ds.content_hit("nothing private here at all")

    # (5) the production digest file exists and discloses nothing (all data lines are salted digests).
    prod = HASHES.read_text(encoding="utf-8")
    for raw in prod.splitlines():
        line = raw.strip()
        if (
            not line
            or line.startswith("#")
            or line.startswith("salt=")
            or line.startswith("ngram_max=")
        ):
            continue
        assert re.match(r"^(both|message) [0-9a-f]{64}$", line), (
            f"cleartext in {HASHES.name}: {line!r}"
        )

    print(
        "LEAK-GUARD SELF-TEST PASSED (sentinel token + phrase caught; clean content cleared; no token disclosed)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--self-test" in argv:
        return self_test()

    hashes_path = HASHES
    if "--hashes" in argv:
        i = argv.index("--hashes")
        hashes_path = Path(argv[i + 1])
        del argv[i : i + 2]
    digests = DigestSet.load(hashes_path)

    if "--files" in argv:
        i = argv.index("--files")
        paths = argv[i + 1 :]
        return _report(scan_files(paths, digests), "the given files")
    if "--stdin" in argv:
        text = sys.stdin.read()
        key = digests.message_key(text)
        return _report([f"{key} <stdin>"] if key else [], "standard input")
    if "--diff" in argv:
        i = argv.index("--diff")
        base = argv[i + 1] if i + 1 < len(argv) else ""
        return _report(
            scan_diff(base, digests),
            f"content and messages added since {base or 'the base'}",
        )

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
