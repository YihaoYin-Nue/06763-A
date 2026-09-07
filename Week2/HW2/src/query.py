# uv run python src/query.py

import re
import time
from pathlib import Path

from load import connect

QUERIES = Path("sql/queries.sql")
PREVIEW = 5


def blocks(body):
    parts = re.split(r"^--\s*name:\s*(\S+)\s*$", body, flags=re.M)
    return list(zip(parts[1::2], parts[2::2]))    # ['', name, sql, name, sql, ...]


# ---------- Run every named query ----------

def main():
    conn = connect()
    for name, sql in blocks(QUERIES.read_text()):
        t = time.perf_counter()
        cur = conn.execute(sql.strip().rstrip(";"))
        rows = cur.fetchall()
        ms = (time.perf_counter() - t) * 1000

        print(f"\n## {name}   {len(rows):,} rows, {ms:,.0f} ms")
        print(" | ".join(d[0] for d in cur.description))
        for r in rows[:PREVIEW]:
            print(" | ".join(str(x) for x in r))
    conn.close()


if __name__ == "__main__":
    main()
