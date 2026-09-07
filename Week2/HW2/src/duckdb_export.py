# uv run python src/duckdb_export.py

import shutil
import time
from pathlib import Path

import duckdb

DB = "lab.db"
OUT = Path("parquet/readings")
PARQUET = "read_parquet('parquet/readings/**/*.parquet', hive_partitioning = true)"
# hive_partitioning restores `date`, which exists only as a directory name

# query (a): DuckDB has a real TIMESTAMP type, so the text is cast rather than sliced
A = """
SELECT sensor_id,
       strftime(ts::TIMESTAMP, '%Y-%m-%d %H:00:00') AS hour,
       count(*)             AS n,
       round(avg(value), 2) AS avg_temp
FROM {src}
WHERE variable = 'temperature'
GROUP BY ALL
"""

# query (c): the RANGE frame is ordered by ts_unix here too, never by the ts text
C = """
SELECT sensor_id, ts, value AS voltage,
       round(avg(value) OVER (PARTITION BY sensor_id ORDER BY ts_unix
                              RANGE BETWEEN 3600 PRECEDING AND CURRENT ROW), 4)
                                   AS voltage_1h_avg,
       count(*)         OVER (PARTITION BY sensor_id ORDER BY ts_unix
                              RANGE BETWEEN 3600 PRECEDING AND CURRENT ROW)
                                   AS n_in_window
FROM {src}
WHERE variable = 'voltage'
"""


# ---------- Export to date-partitioned Parquet ----------

def export(con):
    if OUT.exists():
        shutil.rmtree(OUT)   # a partial tree left by an earlier run would be re-read
    OUT.parent.mkdir(parents=True, exist_ok=True)
    t = time.perf_counter()
    # date is the first 10 characters of ts, and becomes the partition directory name
    con.execute(f"""
        COPY (SELECT *, CAST(substr(ts, 1, 10) AS DATE) AS date FROM lab.readings)
        TO '{OUT.as_posix()}' (FORMAT parquet, PARTITION_BY (date))
    """)
    return time.perf_counter() - t


def timed(con, sql):
    t = time.perf_counter()
    rows = con.execute(sql).fetchall()
    return rows, (time.perf_counter() - t) * 1000


# ---------- Reproduce (a) and (c) against the Parquet ----------

def main():
    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{DB}' AS lab (TYPE sqlite, READ_ONLY)")   # no Python between the two engines

    export_s = export(con)
    parquet_mb = sum(f.stat().st_size for f in OUT.rglob("*.parquet")) / 1e6
    print(f"export     {export_s:,.1f} s into {len(list(OUT.glob('date=*')))} date partitions")
    print(f"on disk    lab.db {Path(DB).stat().st_size / 1e6:,.0f} MB, "
          f"parquet {parquet_mb:,.0f} MB")

    a_rows, a_ms = timed(con, A.format(src=PARQUET))
    print(f"(a)        {len(a_rows):,} hourly buckets, {a_ms:,.0f} ms")

    c_rows, c_ms = timed(con, C.format(src=PARQUET))
    window = [r[4] for r in c_rows]              # ordering the frame by text would pin every window at 1
    print(f"(c)        {len(c_rows):,} rows, {c_ms:,.0f} ms, "
          f"n_in_window {min(window)}-{max(window)}")
    con.close()


if __name__ == "__main__":
    main()
