"""Export lab.db to date-partitioned Parquet, then answer (a) and (c) from the
Parquet with DuckDB and check them against SQLite.

    uv run python src/duckdb_export.py
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import duckdb

DB = "lab.db"
OUT = Path("parquet/readings")
RESULTS = Path("results")
PARQUET = "read_parquet('parquet/readings/**/*.parquet', hive_partitioning = true)"

A = """
SELECT sensor_id,
       strftime(ts::TIMESTAMP, '%Y-%m-%d %H:00:00') AS hour,
       count(*)             AS n,
       round(avg(value), 2) AS avg_temp
FROM {src}
WHERE variable = 'temperature'
GROUP BY ALL
"""

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


def main():
    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{DB}' AS lab (TYPE sqlite, READ_ONLY)")

    if OUT.exists():
        shutil.rmtree(OUT)
    t = time.perf_counter()
    con.execute(f"""
        COPY (SELECT *, CAST(substr(ts, 1, 10) AS DATE) AS date FROM lab.readings)
        TO '{OUT.as_posix()}' (FORMAT parquet, PARTITION_BY (date))
    """)
    export_s = time.perf_counter() - t

    files = sorted(OUT.rglob("*.parquet"))
    par_mb = sum(f.stat().st_size for f in files) / 1e6
    db_mb = Path(DB).stat().st_size / 1e6

    t = time.perf_counter()
    day_rows = con.execute(f"SELECT count(*) FROM {PARQUET} "
                           f"WHERE date = DATE '2004-03-09'").fetchone()[0]
    day_ms = (time.perf_counter() - t) * 1000

    agree = con.execute(f"""
        WITH p AS ({A.format(src=PARQUET)}), s AS ({A.format(src='lab.readings')})
        SELECT (SELECT count(*) FROM p), (SELECT count(*) FROM s), count(*),
               max(abs(p.avg_temp - s.avg_temp))
        FROM p JOIN s USING (sensor_id, hour)
    """).fetchone()

    t = time.perf_counter()
    c_rows = con.execute(C.format(src=PARQUET) +
                         " ORDER BY sensor_id, ts_unix").fetchall()
    c_ms = (time.perf_counter() - t) * 1000

    report = {
        "export_seconds": round(export_s, 1),
        "partitions": len(list(OUT.glob("date=*"))),
        "parquet_files": len(files),
        "parquet_mb": round(par_mb),
        "lab_db_mb": round(db_mb),
        "smaller_by": round(db_mb / par_mb, 1),
        "one_day_rows": day_rows,
        "one_day_ms": round(day_ms),
        "a_parquet_hours": agree[0],
        "a_sqlite_hours": agree[1],
        "a_matched": agree[2],
        "a_max_abs_diff": agree[3],
        "c_rows": len(c_rows),
        "c_ms": round(c_ms),
        "c_n_in_window_min": min(r[4] for r in c_rows),
        "c_n_in_window_max": max(r[4] for r in c_rows),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "export_report.json").write_text(json.dumps(report, indent=2))
    for key, value in report.items():
        print(f"{key:<24}{value:>14,}" if isinstance(value, int)
              else f"{key:<24}{value:>14}")
    con.close()


if __name__ == "__main__":
    main()
