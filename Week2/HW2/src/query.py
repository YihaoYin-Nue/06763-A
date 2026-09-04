"""Run every `-- name:` block in sql/queries.sql and save the results.

    uv run python src/query.py
"""
from __future__ import annotations

import csv
import re
import time
from pathlib import Path

from load import connect

QUERIES = Path("sql/queries.sql")
OUT = Path("results")
MAX_CSV_ROWS = 5_000


def blocks(text):
    parts = re.split(r"^--\s*name:\s*(\S+)\s*$", text, flags=re.M)
    return list(zip(parts[1::2], parts[2::2]))


def main():
    conn = connect()
    OUT.mkdir(exist_ok=True)
    lines = []
    for name, sql in blocks(QUERIES.read_text()):
        t = time.perf_counter()
        cur = conn.execute(sql.strip().rstrip(";"))
        rows = cur.fetchall()
        ms = (time.perf_counter() - t) * 1000
        cols = [d[0] for d in cur.description]
        with (OUT / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(cols)
            writer.writerows(rows[:MAX_CSV_ROWS])
        capped = " (csv capped)" if len(rows) > MAX_CSV_ROWS else ""
        lines.append(f"\n## {name}   ({len(rows):,} rows, {ms:,.0f} ms){capped}\n")
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
        lines += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows[:8]]
        print(f"{name:<46}{len(rows):>10,} rows  {ms:>9,.0f} ms")
    (OUT / "queries.md").write_text("\n".join(lines) + "\n")
    conn.close()


if __name__ == "__main__":
    main()
