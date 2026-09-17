# uv run python src/load.py

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DB = Path("lab.db")
DATA = Path("data/data.txt")
ROSTER_FILE = Path("data/mote_locs.txt")
SCHEMA = Path("sql/schema.sql")

CHANNELS = ("temperature", "humidity", "light", "voltage")
BATCH = 200_000
REPEATS = 5
LOW_BATTERY_V = 2.4     # counted and reported, never used to drop a row; REPORT.md says why

INSERT = ("INSERT INTO readings (sensor_id, ts, ts_unix, epoch, variable, value) "
          "VALUES (?,?,?,?,?,?)")

# the natural key and the access path in one statement, built after the load
INDEX = "CREATE UNIQUE INDEX ux_readings ON readings (sensor_id, ts, variable)"
PROBE = "SELECT count(*) FROM readings WHERE sensor_id = ? AND ts >= ? AND ts < ?"
PROBE_ARGS = (22, "2004-03-09 00:00:00", "2004-03-10 00:00:00")


# ---------- Helpers ----------

def connect(path=DB):
    conn = sqlite3.connect(path, isolation_level=None)   # autocommit, so BEGIN/COMMIT are ours
    conn.execute("PRAGMA foreign_keys = ON")             # per connection, and off by default
    return conn


def bench(fn, n=REPEATS):
    fn()                                                 # warm up, then take the best of n
    best = float("inf")
    for _ in range(n):
        t = time.perf_counter()
        fn()
        best = min(best, (time.perf_counter() - t) * 1000)
    return best


def read_roster(path=ROSTER_FILE):
    roster = []
    for entry in path.read_text().splitlines():
        if entry.strip():
            mote, x, y = entry.split()                   # 'moteid x y', whitespace separated
            roster.append((int(mote), float(x), float(y)))
    return roster


def to_instant(day, clock):
    sec, _, frac = clock.partition(".")                  # four rows in data.txt land on a whole second
    ts = f"{day} {sec}.{(frac + '000000')[:6]}"          # pad to fixed width, so the text sorts by time
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).timestamp(), ts


# ---------- Load dataset ----------

def load(conn):
    n = dict(rows=0, ragged=0, no_mote=0, off_roster=0, unparseable=0, accepted=0, empty_cells=0)

    roster_rows = read_roster()
    roster = {r[0] for r in roster_rows}
    conn.execute("BEGIN")
    conn.executemany("INSERT INTO sensors (sensor_id, x_m, y_m) VALUES (?,?,?)", roster_rows)
    conn.execute("COMMIT")

    batch = []
    t0 = time.perf_counter()
    conn.execute("BEGIN")                        # one transaction for the whole file, not one per row
    with DATA.open("r", encoding="utf-8", errors="replace") as fh:
        for entry in fh:
            entry = entry.rstrip("\r\n")         # not .strip(): an empty voltage leaves a trailing space
            if not entry:
                continue
            n["rows"] += 1
            f = entry.split(" ")                 # one space; collapsing whitespace shifts the columns left
            if len(f) != 8:
                n["ragged"] += 1
                continue
            if not f[3].isdigit():
                n["no_mote"] += 1
                continue
            mote_id = int(f[3])
            if not (1 <= mote_id <= 54) or mote_id not in roster:
                n["off_roster"] += 1
                continue
            try:
                unix, ts = to_instant(f[0], f[1])
                epoch = int(f[2])
                cells = [None if c == "" else float(c) for c in f[4:8]]
            except ValueError:
                n["unparseable"] += 1
                continue
            n["accepted"] += 1
            n["empty_cells"] += cells.count(None)   # an empty channel is absent, not zero
            batch += [(mote_id, ts, unix, epoch, name, v)
                      for name, v in zip(CHANNELS, cells) if v is not None]
            if len(batch) >= BATCH:
                conn.executemany(INSERT, batch)
                batch.clear()
    conn.executemany(INSERT, batch)
    conn.execute("COMMIT")
    n["load_s"] = round(time.perf_counter() - t0, 1)
    return n


# ---------- Cleaning report ----------

def cleaning_report(conn, n):
    return {
        "rows_in_file": n["rows"],
        "ragged_rows": n["ragged"],
        "rejected_empty_moteid": n["no_mote"],
        "rejected_off_roster": n["off_roster"],
        "rejected_unparseable": n["unparseable"],
        "rows_accepted": n["accepted"],
        "absent_channel_cells": n["empty_cells"],
        "readings_loaded": conn.execute("SELECT count(*) FROM readings").fetchone()[0],
        "voltage_below_2.4V": conn.execute(
            "SELECT count(*) FROM readings WHERE variable = 'voltage' AND value < ?",
            (LOW_BATTERY_V,)).fetchone()[0],
        "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "load_seconds": n["load_s"],
    }


# ---------- Index: one query, before and after ----------

def probe(conn):
    plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + PROBE, PROBE_ARGS))
    rows = conn.execute(PROBE, PROBE_ARGS).fetchone()[0]
    ms = bench(lambda: conn.execute(PROBE, PROBE_ARGS).fetchone())   # EXPLAIN QUERY PLAN reports no timing
    return plan, rows, ms


def index_experiment(conn):
    stages = [("before", *probe(conn))]
    conn.execute(INDEX)
    stages.append(("after", *probe(conn)))
    print(f"\nProbe: sensor {PROBE_ARGS[0]}, {PROBE_ARGS[1][:10]}, best of {REPEATS} warm runs")
    for label, plan, rows, ms in stages:
        print(f"{label:<8}{rows:>8,} rows{ms:>10.2f} ms   {plan}")


# ---------- Run ----------

def main():
    DB.unlink(missing_ok=True)                   # lab.db is rebuilt from data.txt, never edited in place
    conn = connect()
    conn.executescript(SCHEMA.read_text())

    for key, value in cleaning_report(conn, load(conn)).items():
        print(f"{key:<26}{value:>14,}")
    index_experiment(conn)
    conn.close()


if __name__ == "__main__":
    main()
