# uv run python src/comparison.py

import time

import duckdb
import pandas as pd

from load import bench, connect

GLOB = "parquet/readings/**/*.parquet"
NEEDED = ["sensor_id", "variable", "value"]     # the only three columns this question names
LOC = {"pandas": 1, "SQLite": 2, "DuckDB": 2}   # counted by hand from the three answers below


# ---------- The same question, three engines ----------

def pandas_answer(df):
    return df[df["variable"] == "temperature"].groupby("sensor_id")["value"].mean()


def sqlite_answer(conn):
    return conn.execute("SELECT sensor_id, avg(value) FROM readings "
                        "WHERE variable = 'temperature' GROUP BY sensor_id").fetchall()


def duckdb_answer(con):
    return con.execute(f"SELECT sensor_id, avg(value) FROM '{GLOB}' "
                       f"WHERE variable = 'temperature' GROUP BY sensor_id").fetchall()


# ---------- Time them ----------

def main():
    t = time.perf_counter()
    df = pd.read_parquet("parquet/readings", engine="pyarrow", columns=NEEDED)
    read_s = time.perf_counter() - t

    conn = connect()
    con = duckdb.connect()

    print("Query: average temperature per mote. Timed warm, best of 5, the query only.\n")
    print("| engine | query (ms) | lines of code |")
    print("|---|---:|---:|")
    for label, fn, arg in (("pandas", pandas_answer, df),
                           ("SQLite", sqlite_answer, conn),
                           ("DuckDB", duckdb_answer, con)):
        print(f"| {label} | {bench(lambda: fn(arg)):,.1f} | {LOC[label]} |")

    print(f"\npandas first read {len(df):,} rows into RAM, which took {read_s:,.1f} s.")
    conn.close()


if __name__ == "__main__":
    main()
