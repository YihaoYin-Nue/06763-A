"""One analytical question in pandas, SQLite and DuckDB.

    uv run python src/comparison.py
"""
from __future__ import annotations

import inspect
import json
import sqlite3
import time
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

GLOB = "parquet/readings/**/*.parquet"
NEEDED = ["sensor_id", "variable", "value"]
REPEATS = 5


def pandas_answer(df):
    return df[df["variable"] == "temperature"].groupby("sensor_id")["value"].mean()


def sqlite_answer(conn):
    return conn.execute("SELECT sensor_id, avg(value) FROM readings "
                        "WHERE variable = 'temperature' GROUP BY sensor_id").fetchall()


def duckdb_answer(con):
    return con.execute(f"SELECT sensor_id, avg(value) FROM '{GLOB}' "
                       f"WHERE variable = 'temperature' GROUP BY sensor_id").fetchall()


def bench(fn, n=REPEATS):
    fn()
    best = float("inf")
    for _ in range(n):
        t = time.perf_counter()
        fn()
        best = min(best, (time.perf_counter() - t) * 1000)
    return best


def loc(fn):
    body = inspect.getsource(fn).splitlines()[1:]
    return sum(1 for x in body if x.strip() and not x.strip().startswith("#"))


def say(msg):
    print(msg, flush=True)


def column_bytes():
    total = {}
    for path in Path("parquet/readings").rglob("*.parquet"):
        meta = pq.ParquetFile(path).metadata
        for group in range(meta.num_row_groups):
            rg = meta.row_group(group)
            for i in range(rg.num_columns):
                col = rg.column(i)
                total[col.path_in_schema] = (total.get(col.path_in_schema, 0)
                                             + col.total_compressed_size)
    return total


def main():
    say("reading the columns the question needs into pandas ...")
    t = time.perf_counter()
    df = pd.read_parquet("parquet/readings", engine="pyarrow", columns=NEEDED)
    load_ms = (time.perf_counter() - t) * 1000
    say(f"  {len(df):,} rows in {load_ms / 1000:.1f} s")

    conn, con = sqlite3.connect("lab.db"), duckdb.connect()
    build = json.loads(Path("results/load_report.json").read_text())
    sqlite_build_s = build["parse_seconds"] + build["expand_seconds"]

    rows = []
    for label, fn, arg, setup in (
            ("pandas (Parquet -> RAM)", pandas_answer, df, f"{load_ms / 1000:.1f} s read"),
            ("SQLite (lab.db)", sqlite_answer, conn, f"{sqlite_build_s:.1f} s load"),
            ("DuckDB (Parquet)", duckdb_answer, con, "none")):
        say(f"benching {label} ...")
        rows.append((label, bench(lambda: fn(arg)), loc(fn), setup))
        say(f"  {rows[-1][1]:,.1f} ms")

    p = sorted((int(k), float(v)) for k, v in pandas_answer(df).items())
    s = sorted((int(a), float(b)) for a, b in sqlite_answer(conn))
    d = sorted((int(a), float(b)) for a, b in duckdb_answer(con))
    worst_ps = max(abs(a[1] - b[1]) for a, b in zip(p, s))
    worst_pd = max(abs(a[1] - b[1]) for a, b in zip(p, d))

    cols = column_bytes()
    for name, size in sorted(cols.items(), key=lambda kv: -kv[1]):
        say(f"    {name:<12}{size / 1e6:>8,.1f} MB")
    par_mb = sum(cols.values()) / 1e6
    need_mb = sum(v for k, v in cols.items() if k in NEEDED) / 1e6
    db_mb = Path("lab.db").stat().st_size / 1e6

    out = [f"Query: average temperature per mote, over all {len(p)} motes.",
           f"Timed: the query only, warm, best of {REPEATS}. "
           f"'Before it can answer' is what each engine needs from data.txt first.\n",
           "| engine | before it can answer | query (ms) | lines of code |",
           "|---|---|---:|---:|"]
    out += [f"| {a} | {d_} | {b:,.1f} | {c} |" for a, b, c, d_ in rows]
    out += [f"\nOn disk: lab.db {db_mb:,.0f} MB (indexes included), "
            f"Parquet {par_mb:,.0f} MB.",
            f"The {len(NEEDED)} columns this question names are {need_mb:,.0f} MB of "
            f"that {par_mb:,.0f} MB, so the column store reads "
            f"{100 * need_mb / par_mb:.0f}% of the bytes the row store must.",
            f"\nLargest disagreement in the answer: pandas vs SQLite {worst_ps:.3g}, "
            f"pandas vs DuckDB {worst_pd:.3g}."]
    Path("results/three_way.md").write_text("\n".join(out) + "\n")
    print()
    print("\n".join(out))


if __name__ == "__main__":
    main()
