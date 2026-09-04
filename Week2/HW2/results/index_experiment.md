Probe: sensor 22, 2004-03-09, median of 5 warm runs

| index | rows | median ms | EXPLAIN QUERY PLAN |
|---|---:|---:|---|
| no index | 10,452 | 370.24 | `SCAN readings` |
| index on (ts, sensor_id) | 10,452 | 15.01 | `SEARCH readings USING COVERING INDEX ix_probe_ts_first (ts>? AND ts<?)` |
| index on (sensor_id, ts, variable) | 10,452 | 0.26 | `SEARCH readings USING COVERING INDEX ux_readings_natural (sensor_id=? AND ts>? AND ts<?)` |

lab.db 1,376 MB unindexed, 1,376 MB indexed (+0%).
