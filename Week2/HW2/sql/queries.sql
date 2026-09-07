-- name: a_hourly_avg_temperature
-- date_trunc('hour', ts) has no SQLite spelling; strftime over the ISO-8601 text does it
SELECT sensor_id,
       strftime('%Y-%m-%d %H:00:00', ts) AS hour,
       count(*)                          AS n,
       round(avg(value), 2)              AS avg_temp
FROM readings
WHERE variable = 'temperature'
GROUP BY sensor_id, hour
ORDER BY sensor_id, hour;


-- name: b_missing_intervals
-- LEFT JOIN out of the roster: starting FROM readings can only rank motes that reported at all
WITH bounds AS (
    SELECT min(ts_unix) AS t0, max(ts_unix) AS t1 FROM readings
),
observed AS (
    SELECT sensor_id, count(DISTINCT ts) AS n_samples
    FROM readings
    GROUP BY sensor_id
)
-- n_expected = window length / 30 s nominal period
SELECT s.sensor_id,
       coalesce(o.n_samples, 0)                                     AS n_samples,
       CAST((b.t1 - b.t0) / 30.0 AS INT)                            AS n_expected,
       CAST((b.t1 - b.t0) / 30.0 AS INT) - coalesce(o.n_samples, 0) AS n_missing
FROM sensors s
LEFT JOIN observed o ON o.sensor_id = s.sensor_id
CROSS JOIN bounds b
ORDER BY n_missing DESC
LIMIT 5;


-- name: c_rolling_1h_voltage
-- ORDER BY ts_unix, never ts: a RANGE frame over the text column silently windows one row
SELECT sensor_id,
       ts,
       value AS voltage,
       round(avg(value) OVER (PARTITION BY sensor_id ORDER BY ts_unix
                              RANGE BETWEEN 3600 PRECEDING AND CURRENT ROW), 4)
                                                              AS voltage_1h_avg,
       count(*)         OVER (PARTITION BY sensor_id ORDER BY ts_unix
                              RANGE BETWEEN 3600 PRECEDING AND CURRENT ROW)
                                                              AS n_in_window
FROM readings
WHERE variable = 'voltage'
ORDER BY sensor_id, ts_unix;


-- name: d_outside_plausible_range
-- indoor office 0-50 C, %RH is a percentage, the mote saturates near 1800 lux, voltage ranging from 2-3 V
SELECT variable,
       count(*)                  AS n_readings,
       count(DISTINCT sensor_id) AS n_sensors,
       min(value)                AS min_value,
       max(value)                AS max_value
FROM readings
WHERE (variable = 'temperature' AND value NOT BETWEEN 0.0 AND 50.0)
   OR (variable = 'humidity'    AND value NOT BETWEEN 0.0 AND 100.0)
   OR (variable = 'light'       AND value NOT BETWEEN 0.0 AND 2000.0)
   OR (variable = 'voltage'     AND value NOT BETWEEN 2.0 AND 3.0)
GROUP BY variable
ORDER BY n_readings DESC;
