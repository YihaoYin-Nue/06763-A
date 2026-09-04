"""L3 demo, end to end, as a plain script.

Same data, schema, and SQL as l03-sql-timeseries.ipynb, but runnable with
`python run_l03.py` and writing every result to disk.
"""
import io, json, os, time, zipfile, urllib.request
from pathlib import Path

import pandas as pd
import psycopg
import sqlalchemy as sa

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("L3_OUT", HERE / "results"))
OUT.mkdir(parents=True, exist_ok=True)
CACHE = Path(os.environ.get("L3_CACHE", HERE / ".cache"))
CACHE.mkdir(parents=True, exist_ok=True)

DSN = os.environ.get("L3_DSN", "postgresql+psycopg://demo:demo@localhost:5432/sensors")
engine = sa.create_engine(DSN)
stats = {}


def log(*a):
    print(*a, flush=True)


def q(sql):
    with engine.connect() as c:
        return pd.read_sql_query(sa.text(sql), c)


def save(name, df, title, note):
    df.to_csv(OUT / f"{name}.csv", index=False)
    md = [f"## {title}", "", note, "", df.to_markdown(index=False), ""]
    (OUT / f"{name}.md").write_text("\n".join(md), encoding="utf-8")
    log(f"\n--- {title} ---")
    log(df.to_string(index=False))


# ---------------------------------------------------------------- 1. fetch
txt = CACHE / "data.txt"
URL = "https://raw.githubusercontent.com/linsea423/Intel_Lab_Data/master/data.zip"
if not txt.exists():
    log("downloading", URL)
    with urllib.request.urlopen(URL) as r:
        buf = r.read()
    with zipfile.ZipFile(io.BytesIO(buf)) as z:
        txt.write_bytes(z.read("data.txt"))

cols = ["date", "time", "epoch", "moteid",
        "temperature", "humidity", "light", "voltage"]
t0 = time.time()
raw = pd.read_csv(txt, sep=r"\s+", names=cols, header=None,
                  engine="c", on_bad_lines="skip")
stats["parse_seconds"] = round(time.time() - t0, 1)
stats["raw_rows"] = int(len(raw))
log(f"{len(raw):,} raw rows in {stats['parse_seconds']}s")

# ---------------------------------------------------------------- 2. clean
df = raw.dropna(subset=["moteid"]).copy()
df["moteid"] = df["moteid"].astype(int)
df = df[df.moteid.between(1, 54)]
df["ts"] = pd.to_datetime(df["date"] + " " + df["time"],
                          format="mixed", errors="coerce")
df = df.dropna(subset=["ts"])

long = (df.melt(id_vars=["moteid", "ts"],
                value_vars=["temperature", "humidity", "light", "voltage"],
                var_name="variable", value_name="value")
          .dropna(subset=["value"])
          .rename(columns={"moteid": "sensor_id"})
          .drop_duplicates(["sensor_id", "ts", "variable"]))
stats["tidy_rows"] = int(len(long))
stats["n_sensors"] = int(long.sensor_id.nunique())
stats["rows_dropped_cleaning"] = int(len(raw) * 4 - len(long))
log(f"{len(long):,} tidy readings, {long.sensor_id.nunique()} sensors")

# ---------------------------------------------------------------- 3. schema
SCHEMA = """
DROP TABLE IF EXISTS readings;
DROP TABLE IF EXISTS variables;
DROP TABLE IF EXISTS sensors;

CREATE TABLE sensors (
    sensor_id int PRIMARY KEY,
    x_m double precision,
    y_m double precision
);

CREATE TABLE variables (
    variable text PRIMARY KEY,
    unit text NOT NULL,
    lo double precision,
    hi double precision
);

CREATE TABLE readings (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sensor_id int NOT NULL REFERENCES sensors (sensor_id),
    ts        timestamptz NOT NULL,
    variable  text NOT NULL REFERENCES variables (variable),
    value     double precision
);
"""
with engine.begin() as c:
    c.execute(sa.text(SCHEMA))
log("schema created")

pd.DataFrame({"sensor_id": sorted(long.sensor_id.unique())}).to_sql(
    "sensors", engine, if_exists="append", index=False)
pd.DataFrame([("temperature", "degC", -20, 60),
              ("humidity", "%RH", 0, 100),
              ("light", "lux", 0, 2000),
              ("voltage", "V", 2.0, 3.0)],
             columns=["variable", "unit", "lo", "hi"]).to_sql(
    "variables", engine, if_exists="append", index=False)

# ---------------------------------------------------------------- 4. COPY
raw_dsn = engine.url.render_as_string(hide_password=False).replace(
    "postgresql+psycopg://", "postgresql://")
buf = io.StringIO()
long[["sensor_id", "ts", "variable", "value"]].to_csv(buf, index=False, header=False)
buf.seek(0)
t0 = time.time()
with psycopg.connect(raw_dsn) as conn:
    with conn.cursor() as cur:
        with cur.copy("COPY readings (sensor_id, ts, variable, value) "
                      "FROM STDIN WITH (FORMAT csv)") as cp:
            for chunk in iter(lambda: buf.read(1 << 20), ""):
                cp.write(chunk)
    conn.commit()
stats["copy_seconds"] = round(time.time() - t0, 1)
stats["rows_loaded"] = int(q("SELECT count(*) AS n FROM readings").n[0])
log(f"COPY loaded {stats['rows_loaded']:,} rows in {stats['copy_seconds']}s")

# ------------------------------------------------------- 5. FK is not decor
from sqlalchemy.exc import IntegrityError
try:
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO readings (sensor_id, ts, variable, value) "
                          "VALUES (999, now(), 'temperature', 21.0)"))
    stats["fk_rejected"] = False
    fk_msg = "NOT rejected - unexpected"
except IntegrityError as e:
    stats["fk_rejected"] = True
    fk_msg = str(e.orig).splitlines()[0]
log("FK check:", fk_msg)

# ---------------------------------------------------------------- 6. Q1-Q5
save("q1_hourly_avg_temperature", q("""
SELECT sensor_id,
       date_trunc('hour', ts) AS hour,
       round(avg(value)::numeric, 2) AS avg_temp
FROM   readings
WHERE  variable = 'temperature'
GROUP  BY sensor_id, hour
ORDER  BY sensor_id, hour
LIMIT  8
"""), "Q1 - hourly average temperature per sensor",
  "`date_trunc` buckets irregular readings into clean hours; `GROUP BY` does the rest.")

save("q2_dropouts", q("""
SELECT sensor_id, count(*) AS n_temp_readings
FROM   readings
WHERE  variable = 'temperature'
GROUP  BY sensor_id
HAVING count(*) < 30000
ORDER  BY n_temp_readings
"""), "Q2 - which motes dropped out?",
  "A mote that went quiet reported far fewer times. `HAVING` filters on the aggregate. "
  "(Notebook shows LIMIT 8; every qualifying mote is listed here.)")

save("q3_gaps", q("""
WITH gaps AS (
  SELECT sensor_id, ts,
         ts - lag(ts) OVER (PARTITION BY sensor_id ORDER BY ts) AS gap
  FROM   readings
  WHERE  variable = 'temperature'
)
SELECT sensor_id, ts, gap
FROM   gaps
WHERE  gap > interval '1 hour'
ORDER  BY gap DESC
LIMIT  25
"""), "Q3 - gaps in reporting, via lag()",
  "Motes aim for one reading every ~31 s, so large gaps locate the dropouts in time. "
  "(Notebook shows LIMIT 8; top 25 here.)")

save("q4_rolling_voltage", q("""
SELECT sensor_id, ts,
       round(value::numeric, 3) AS voltage,
       round(avg(value) OVER (
         PARTITION BY sensor_id ORDER BY ts
         RANGE BETWEEN interval '1 hour' PRECEDING AND CURRENT ROW
       )::numeric, 3) AS voltage_1h_avg
FROM   readings
WHERE  variable = 'voltage' AND sensor_id = 1
ORDER  BY ts
LIMIT  8
"""), "Q4 - rolling 1-hour average voltage",
  "A time-based `RANGE` frame is the honest choice when sampling is irregular.")

save("q5_impossible_readings", q("""
WITH t AS (
  SELECT sensor_id, ts, value AS temp FROM readings
  WHERE variable = 'temperature'
), v AS (
  SELECT sensor_id, ts, value AS volt FROM readings
  WHERE variable = 'voltage'
)
SELECT
  count(*) FILTER (WHERE temp < 0 OR temp > 50) AS impossible,
  round(100.0 * avg((temp < 0 OR temp > 50)::int), 1) AS pct_impossible,
  round(100.0 * (count(*) FILTER (WHERE (temp < 0 OR temp > 50)
                                    AND volt < 2.4))
        / nullif(count(*) FILTER (WHERE temp < 0 OR temp > 50), 0), 1)
        AS pct_of_impossible_below_2v4
FROM t JOIN v USING (sensor_id, ts)
"""), "Q5 - impossible temperatures, and what predicts them",
  "Nearly every physically impossible temperature comes from a mote whose battery "
  "had already fallen below ~2.4 V. Voltage is a data-quality signal.")

# ------------------------------------------------------------- 7. indexing
RANGE_Q = """
SELECT count(*) FROM readings
WHERE  sensor_id = 5
  AND  ts BETWEEN '2004-03-15' AND '2004-03-16'
"""


def explain(sql):
    return "\n".join(q("EXPLAIN ANALYZE " + sql)["QUERY PLAN"])


before = explain(RANGE_Q)
(OUT / "explain_before.txt").write_text(before, encoding="utf-8")
log("\n--- EXPLAIN ANALYZE, no index ---\n" + before)

sz = q("""SELECT pg_size_pretty(pg_total_relation_size('readings')) AS readings_total,
                 pg_size_pretty(pg_relation_size('readings'))       AS heap,
                 pg_size_pretty(pg_database_size('sensors'))        AS db""")
stats["size_before_index"] = sz.to_dict("records")[0]

t0 = time.time()
with engine.begin() as c:
    c.execute(sa.text("CREATE INDEX IF NOT EXISTS readings_sensor_ts "
                      "ON readings (sensor_id, ts)"))
    c.execute(sa.text("ANALYZE readings"))
stats["index_build_seconds"] = round(time.time() - t0, 1)

after = explain(RANGE_Q)
(OUT / "explain_after.txt").write_text(after, encoding="utf-8")
log("\n--- EXPLAIN ANALYZE, with (sensor_id, ts) index ---\n" + after)

sz2 = q("""SELECT pg_size_pretty(pg_database_size('sensors'))  AS db,
                  pg_size_pretty(pg_indexes_size('readings'))  AS all_indexes""")
stats["size_after_index"] = sz2.to_dict("records")[0]


def exec_ms(plan):
    for line in plan.splitlines():
        if line.strip().startswith("Execution Time:"):
            return float(line.split(":")[1].strip().split()[0])
    return None


stats["exec_ms_before"] = exec_ms(before)
stats["exec_ms_after"] = exec_ms(after)
if stats["exec_ms_before"] and stats["exec_ms_after"]:
    stats["speedup"] = round(stats["exec_ms_before"] / stats["exec_ms_after"], 1)

(OUT / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
log("\nstats: " + json.dumps(stats, indent=2))
log("\nDONE")
