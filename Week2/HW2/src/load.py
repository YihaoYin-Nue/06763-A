"""Build lab.db from data/data.txt and data/mote_locs.txt, then measure the query
plan before and after indexing.

    uv run python src/load.py
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DB = Path("lab.db")
DATA = Path("data/data.txt")
ROSTER_FILE = Path("data/mote_locs.txt")
SCHEMA = Path("sql/schema.sql")
INDEXES = Path("sql/indexes.sql")
RESULTS = Path("results")

CHANNELS = ("temperature", "humidity", "light", "voltage")
BATCH = 100_000
LOW_BATTERY_V = 2.4
PROBE_DAY = ("2004-03-09 00:00:00", "2004-03-10 00:00:00")
PROBE = "SELECT count(*) FROM readings WHERE sensor_id = ? AND ts >= ? AND ts < ?"
REPEATS = 5

INSERT_WIDE = ("INSERT OR IGNORE INTO readings_wide "
               "(sensor_id, ts, ts_unix, epoch, temperature, humidity, light, voltage) "
               "VALUES (?,?,?,?,?,?,?,?)")
EXPAND = ("INSERT INTO readings (sensor_id, ts, ts_unix, epoch, variable, value) "
          "SELECT sensor_id, ts, ts_unix, epoch, '{c}', {c} "
          "FROM readings_wide WHERE {c} IS NOT NULL")


def connect(path=DB):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def parse_roster(path):
    out = []
    for entry in path.read_text().splitlines():
        if entry.strip():
            mote, x, y = entry.split()
            out.append((int(mote), float(x), float(y)))
    return out


def to_instant(day, clock, cache):
    base = cache.get(day)
    if base is None:
        base = datetime.strptime(day, "%Y-%m-%d").replace(
            tzinfo=timezone.utc).timestamp()
        cache[day] = base
    hh, mm, rest = clock[:2], clock[3:5], clock[6:]
    whole, _, frac = rest.partition(".")
    unix = base + int(hh) * 3600 + int(mm) * 60 + int(whole) + float("0." + (frac or "0"))
    return unix, f"{day} {hh}:{mm}:{whole}.{(frac + '000000')[:6]}"


def load(conn):
    n = Counter()
    cache, batch = {}, []
    roster_rows = parse_roster(ROSTER_FILE)
    roster = {r[0] for r in roster_rows}

    conn.execute("BEGIN")
    conn.executemany("INSERT INTO sensors (sensor_id, x_m, y_m) VALUES (?,?,?)",
                     roster_rows)
    conn.execute("COMMIT")

    t0 = time.perf_counter()
    conn.execute("BEGIN")
    with DATA.open("r", encoding="utf-8", errors="replace") as fh:
        for entry in fh:
            entry = entry.rstrip("\r\n")
            if not entry:
                continue
            n["rows"] += 1
            f = entry.split(" ")
            if len(f) != 8:
                n["ragged"] += 1
                continue
            if not f[3]:
                n["no_mote"] += 1
                continue
            try:
                mote_id = int(f[3])
            except ValueError:
                n["no_mote"] += 1
                continue
            if not (1 <= mote_id <= 54) or mote_id not in roster:
                n["off_roster"] += 1
                continue
            try:
                unix, ts = to_instant(f[0], f[1], cache)
                epoch = int(f[2])
                cells = [None if c == "" else float(c) for c in f[4:8]]
            except ValueError:
                n["unparseable"] += 1
                continue
            n["missing_cells"] += sum(c is None for c in cells)
            batch.append((mote_id, ts, unix, epoch, *cells))
            n["offered"] += 1
            if len(batch) >= BATCH:
                conn.executemany(INSERT_WIDE, batch)
                batch.clear()
    if batch:
        conn.executemany(INSERT_WIDE, batch)
    conn.execute("COMMIT")
    n["parse_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    conn.execute("BEGIN")
    for name in CHANNELS:
        conn.execute(EXPAND.format(c=name))
    conn.execute("COMMIT")
    n["expand_ms"] = (time.perf_counter() - t0) * 1000
    return n


def summarise(conn, n):
    wide = conn.execute("SELECT count(*) FROM readings_wide").fetchone()[0]
    long = conn.execute("SELECT count(*) FROM readings").fetchone()[0]
    lo, hi = conn.execute(
        "SELECT lo, hi FROM variables WHERE variable = 'temperature'").fetchone()
    n_temp, n_bad, n_bad_low = conn.execute(
        "SELECT count(*), "
        "count(*) FILTER (WHERE temperature NOT BETWEEN ? AND ?), "
        "count(*) FILTER (WHERE temperature NOT BETWEEN ? AND ? AND voltage < ?) "
        "FROM readings_wide WHERE temperature IS NOT NULL AND voltage IS NOT NULL",
        (lo, hi, lo, hi, LOW_BATTERY_V)).fetchone()
    return {
        "rows_in_file": n["rows"],
        "ragged": n["ragged"],
        "rejected_empty_moteid": n["no_mote"],
        "rejected_off_roster": n["off_roster"],
        "rejected_unparseable": n["unparseable"],
        "rows_accepted": n["offered"],
        "duplicates_ignored": n["offered"] - wide,
        "wide_rows": wide,
        "long_readings": long,
        "absent_channel_cells": n["missing_cells"],
        "temperature_readings": n_temp,
        "outside_plausible_range": n_bad,
        f"outside_range_and_below_{LOW_BATTERY_V}V": n_bad_low,
        "foreign_key_violations": len(
            conn.execute("PRAGMA foreign_key_check").fetchall()),
        "parse_seconds": round(n["parse_ms"] / 1000, 1),
        "expand_seconds": round(n["expand_ms"] / 1000, 1),
    }


def probe(conn, sensor_id):
    args = (sensor_id, *PROBE_DAY)
    plan = " / ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + PROBE, args))
    conn.execute(PROBE, args).fetchone()
    times = []
    for _ in range(REPEATS):
        t = time.perf_counter()
        rows = conn.execute(PROBE, args).fetchone()[0]
        times.append((time.perf_counter() - t) * 1000)
    return plan, rows, statistics.median(times)


def index_experiment(conn):
    sensor_id = conn.execute(
        "SELECT sensor_id FROM readings WHERE ts >= ? AND ts < ? "
        "GROUP BY sensor_id ORDER BY count(*) DESC LIMIT 1", PROBE_DAY).fetchone()[0]
    unindexed_mb = DB.stat().st_size / 1e6

    stages = [("no index", *probe(conn, sensor_id))]
    conn.execute("CREATE INDEX ix_probe_ts_first ON readings (ts, sensor_id)")
    stages.append(("index on (ts, sensor_id)", *probe(conn, sensor_id)))
    conn.execute("DROP INDEX ix_probe_ts_first")
    conn.executescript(INDEXES.read_text())
    stages.append(("index on (sensor_id, ts, variable)", *probe(conn, sensor_id)))
    indexed_mb = DB.stat().st_size / 1e6

    lines = [f"Probe: sensor {sensor_id}, {PROBE_DAY[0][:10]}, "
             f"median of {REPEATS} warm runs\n",
             "| index | rows | median ms | EXPLAIN QUERY PLAN |",
             "|---|---:|---:|---|"]
    lines += [f"| {label} | {rows:,} | {ms:,.2f} | `{plan}` |"
              for label, plan, rows, ms in stages]
    lines.append(f"\nlab.db {unindexed_mb:,.0f} MB unindexed, {indexed_mb:,.0f} MB "
                 f"indexed (+{100 * (indexed_mb / unindexed_mb - 1):.0f}%).")
    return lines


def main():
    conn = connect()
    conn.execute("PRAGMA synchronous = OFF")
    conn.executescript(SCHEMA.read_text())

    report = summarise(conn, load(conn))
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "load_report.json").write_text(json.dumps(report, indent=2))
    for key, value in report.items():
        print(f"{key:<34}{value:>14,}" if isinstance(value, int)
              else f"{key:<34}{value:>14}")

    lines = index_experiment(conn)
    (RESULTS / "index_experiment.md").write_text("\n".join(lines) + "\n")
    print()
    print("\n".join(lines))
    conn.close()


if __name__ == "__main__":
    main()
