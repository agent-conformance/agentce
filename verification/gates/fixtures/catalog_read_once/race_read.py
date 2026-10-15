"""Runs the Python CLI with a writer racing it (VG-CATALOG-READ-ONCE, 18.171).

    race_read.py <file> <swap-bytes-file|-> <count-file> <agentce args...>

Every open of <file> is counted, and the count is written to <count-file> at exit. Just before a second
open, <file> on disk becomes the bytes of <swap-bytes-file>, as a writer changing it after the engine's
first read would make it, so a second read sees other bytes than the first.
"""

import os
import sys

from agentce import cli

target, swap_path, count_path = os.path.realpath(sys.argv[1]), sys.argv[2], sys.argv[3]
swap = None if swap_path == "-" else open(swap_path, "rb").read()
state = {"count": 0, "writing": False}


def hook(event: str, args: tuple) -> None:
    if event != "open" or state["writing"] or not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    if os.path.realpath(os.fsdecode(args[0])) != target:
        return
    state["count"] += 1
    if state["count"] == 2 and swap is not None:
        state["writing"] = True
        with open(target, "wb") as f:
            f.write(swap)
        state["writing"] = False


sys.addaudithook(hook)
try:
    code = cli.main(sys.argv[4:])
finally:
    state["writing"] = True
    with open(count_path, "w") as f:
        f.write(str(state["count"]))
sys.exit(code)
