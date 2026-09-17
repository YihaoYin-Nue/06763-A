from __future__ import annotations

import hashlib
import os
import subprocess
import sys

from contract import PROCESSED, RESULTS, ROOT, raw_files

REPORT = RESULTS / "idempotence.txt"


def sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def outputs() -> dict[str, str]:
    files = sorted(p for p in [*PROCESSED.glob("*"), *RESULTS.glob("*")] if p.is_file() and p != REPORT)
    return {p.relative_to(ROOT).as_posix(): sha256(p) for p in files}


def run_pipeline() -> None:
    """The whole pipeline, in a fresh interpreter, as a person would run it."""
    done = subprocess.run([sys.executable, str(ROOT / "src" / "pipeline.py")], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if done.returncode:
        raise SystemExit(done.stdout + done.stderr)


def main() -> str:
    raw_before = {p.name: sha256(p) for p in raw_files()}
    run_pipeline()
    first = outputs()
    run_pipeline()
    second = outputs()
    raw_after = {p.name: sha256(p) for p in raw_files()}

    lines = ["idempotence: src/pipeline.py run twice on the same raw capture", "",
             "  run 2 vs run 1   sha256 (first 16)  file"]
    for name in sorted(set(first) | set(second)):
        a, b = first.get(name), second.get(name)
        lines.append(f"  {'same   ' if a == b else 'CHANGED'}          {(b or a or '-')[:16]}   {name}")
    identical = first == second
    lines += ["",
              *(f"  raw {name}: sha256 {raw_before[name][:16]} before, "
                f"{'unchanged' if raw_after.get(name) == raw_before[name] else 'CHANGED'} after" for name in raw_before),
              "",
              f"all {len(second)} outputs byte-identical across the two runs: {'yes' if identical else 'NO'}"]
    report = "\n".join(lines)
    REPORT.write_text(report + "\n", encoding="utf-8")
    if not identical or raw_before != raw_after:
        raise SystemExit(report + "\n\nNOT IDEMPOTENT")
    return report


if __name__ == "__main__":
    print(main())
