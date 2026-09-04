import json, os, time
from pathlib import Path
import pandas as pd, sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

OUT = Path(os.environ["L3_OUT"]); OUT.mkdir(parents=True, exist_ok=True)
DSN = os.environ.get("L3_DSN","postgresql+psycopg://demo:demo@localhost:5432/sensors")
engine = sa.create_engine(DSN)
stats = json.load(open(Path(__file__).resolve().parent/"prep_stats.json"))
stats.pop("sensor_ids", None)

def q(sql):
    with engine.connect() as c:
        return pd.read_sql_query(sa.text(sql), c)

def save(name, df, title, note):
    df.to_csv(OUT/f"{name}.csv", index=False)
    (OUT/f"{name}.md").write_text(f"## {title}\n\n{note}\n\n{df.to_markdown(index=False)}\n", encoding="utf-8")
    print(f"\n--- {title} ---\n{df.to_string(index=False)}", flush=True)

stats["rows_loaded"] = int(q("SELECT count(*) AS n FROM readings").n[0])

try:
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO readings (sensor_id, ts, variable, value) VALUES (999, now(), 'temperature', 21.0)"))
    stats["fk_rejected"]=False; fk="NOT rejected (unexpected)"
except IntegrityError as e:
    stats["fk_rejected"]=True; fk=str(e.orig).splitlines()[0]
print("FK check:", fk, flush=True)
stats["fk_message"]=fk

t=time.time(); save("q1_hourly_avg_temperature", q("""
SELECT sensor_id, date_trunc('hour', ts) AS hour, round(avg(value)::numeric, 2) AS avg_temp
FROM readings WHERE variable = 'temperature'
GROUP BY sensor_id, hour ORDER BY sensor_id, hour LIMIT 8"""),
"Q1 - hourly average temperature per sensor",
"`date_trunc` buckets the irregular readings into clean hours; `GROUP BY` does the rest.")
stats["q1_seconds"]=round(time.time()-t,1)

t=time.time(); save("q2_dropouts", q("""
SELECT sensor_id, count(*) AS n_temp_readings
FROM readings WHERE variable = 'temperature'
GROUP BY sensor_id HAVING count(*) < 30000 ORDER BY n_temp_readings"""),
"Q2 - which motes dropped out?",
"A mote that went quiet reported far fewer times than a healthy one; `HAVING` filters on the aggregate. The notebook shows `LIMIT 8` - every qualifying mote is listed here.")
stats["q2_seconds"]=round(time.time()-t,1)
stats["n_dropout_motes"]=int(len(pd.read_csv(OUT/"q2_dropouts.csv")))

t=time.time(); save("q3_gaps", q("""
WITH gaps AS (
  SELECT sensor_id, ts, ts - lag(ts) OVER (PARTITION BY sensor_id ORDER BY ts) AS gap
  FROM readings WHERE variable = 'temperature')
SELECT sensor_id, ts, gap FROM gaps WHERE gap > interval '1 hour'
ORDER BY gap DESC LIMIT 25"""),
"Q3 - gaps in reporting, with a window function",
"`lag` reaches back to a sensor's previous reading, so `ts - lag(ts)` is the gap since it last reported. Motes aim for one reading every ~31 s, so the large gaps are the dropouts, located exactly in time. Notebook shows 8; top 25 here.")
stats["q3_seconds"]=round(time.time()-t,1)

t=time.time(); save("q4_rolling_voltage", q("""
SELECT sensor_id, ts, round(value::numeric, 3) AS voltage,
       round(avg(value) OVER (PARTITION BY sensor_id ORDER BY ts
         RANGE BETWEEN interval '1 hour' PRECEDING AND CURRENT ROW)::numeric, 3) AS voltage_1h_avg
FROM readings WHERE variable = 'voltage' AND sensor_id = 1 ORDER BY ts LIMIT 8"""),
"Q4 - a rolling 1-hour average voltage",
"Same window machinery with an aggregate and a time-based frame. Because the sampling is irregular, a `RANGE` frame measured in time is the honest choice, not a fixed number of rows.")
stats["q4_seconds"]=round(time.time()-t,1)

t=time.time(); save("q5_impossible_readings", q("""
WITH t AS (SELECT sensor_id, ts, value AS temp FROM readings WHERE variable = 'temperature'),
     v AS (SELECT sensor_id, ts, value AS volt FROM readings WHERE variable = 'voltage')
SELECT count(*) FILTER (WHERE temp < 0 OR temp > 50) AS impossible,
       round(100.0 * avg((temp < 0 OR temp > 50)::int), 1) AS pct_impossible,
       round(100.0 * (count(*) FILTER (WHERE (temp < 0 OR temp > 50) AND volt < 2.4))
             / nullif(count(*) FILTER (WHERE temp < 0 OR temp > 50), 0), 1) AS pct_of_impossible_below_2v4
FROM t JOIN v USING (sensor_id, ts)"""),
"Q5 - the impossible readings, and what predicts them",
"Joining each temperature reading to the same mote's voltage at the same instant shows why roughly a fifth of readings are physically impossible: nearly every one comes from a mote whose battery had already fallen below ~2.4 V. Voltage is a data-quality signal, not just housekeeping.")
stats["q5_seconds"]=round(time.time()-t,1)

RANGE_Q = """
SELECT count(*) FROM readings
WHERE  sensor_id = 5
  AND  ts BETWEEN '2004-03-15' AND '2004-03-16'
"""
def explain(sql): return "\n".join(q("EXPLAIN ANALYZE "+sql)["QUERY PLAN"])
def exec_ms(p):
    for l in p.splitlines():
        if l.strip().startswith("Execution Time:"): return float(l.split(":")[1].strip().split()[0])

with engine.begin() as c: c.execute(sa.text("DROP INDEX IF EXISTS readings_sensor_ts")); c.execute(sa.text("ANALYZE readings"))
before = explain(RANGE_Q); (OUT/"explain_before.txt").write_text(before, encoding="utf-8")
print("\n--- EXPLAIN ANALYZE, no index ---\n"+before, flush=True)
stats["size_before_index"] = q("""SELECT pg_size_pretty(pg_total_relation_size('readings')) AS readings_total,
  pg_size_pretty(pg_relation_size('readings')) AS heap, pg_size_pretty(pg_database_size('sensors')) AS db""").to_dict("records")[0]

t=time.time()
with engine.begin() as c:
    c.execute(sa.text("CREATE INDEX IF NOT EXISTS readings_sensor_ts ON readings (sensor_id, ts)"))
    c.execute(sa.text("ANALYZE readings"))
stats["index_build_seconds"]=round(time.time()-t,1)

after = explain(RANGE_Q); (OUT/"explain_after.txt").write_text(after, encoding="utf-8")
print("\n--- EXPLAIN ANALYZE, with (sensor_id, ts) index ---\n"+after, flush=True)
stats["size_after_index"] = q("""SELECT pg_size_pretty(pg_database_size('sensors')) AS db,
  pg_size_pretty(pg_indexes_size('readings')) AS all_indexes""").to_dict("records")[0]
stats["exec_ms_before"]=exec_ms(before); stats["exec_ms_after"]=exec_ms(after)
if stats["exec_ms_before"] and stats["exec_ms_after"]:
    stats["speedup"]=round(stats["exec_ms_before"]/stats["exec_ms_after"],1)
stats["scan_before"]=next((l.strip() for l in before.splitlines() if "Scan" in l), "")
stats["scan_after"]=next((l.strip() for l in after.splitlines() if "Scan" in l), "")

json.dump(stats, open(OUT/"stats.json","w"), indent=2)
print("\n"+json.dumps(stats, indent=2), flush=True)
print("DONE", flush=True)
