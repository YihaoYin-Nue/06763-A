-- name: a_hourly_avg_temperature
-- Both averages on purpose: 17.7% of the temperature readings are outside the
-- plausible band, and the pair shows what including them does to the hour.
SELECT r.sensor_id,
       strftime('%Y-%m-%d %H:00:00', r.ts)                                 AS hour,
       count(*)                                                            AS n,
       round(avg(r.value), 2)                                              AS avg_temp_raw,
       round(avg(CASE WHEN r.value BETWEEN v.lo AND v.hi THEN r.value END), 2)
                                                                           AS avg_temp_plausible
FROM readings r
JOIN variables v ON v.variable = r.variable
WHERE r.variable = 'temperature'
GROUP BY r.sensor_id, hour
ORDER BY r.sensor_id, hour;


-- name: b_missing_intervals
-- Ranked FROM the roster, not from the readings.  A query that starts FROM
-- readings can only rank motes that produced readings, and mote 5 produced 35.
WITH bounds AS (
    SELECT min(ts_unix) AS t0, max(ts_unix) AS t1 FROM readings
),
observed AS (
    SELECT sensor_id, count(DISTINCT ts) AS n_samples
    FROM readings
    GROUP BY sensor_id
)
SELECT s.sensor_id,
       coalesce(o.n_samples, 0)                                       AS n_samples,
       CAST((b.t1 - b.t0) / 30.0 AS INT)                              AS n_expected,
       CAST((b.t1 - b.t0) / 30.0 AS INT) - coalesce(o.n_samples, 0)   AS n_missing,
       round(100.0 * coalesce(o.n_samples, 0) * 30.0 / (b.t1 - b.t0), 2)
                                                                      AS pct_delivered
FROM sensors s
LEFT JOIN observed o ON o.sensor_id = s.sensor_id
CROSS JOIN bounds b
ORDER BY n_missing DESC
LIMIT 5;


-- name: c_rolling_1h_voltage
-- ORDER BY ts_unix, never ts.  A RANGE frame over the text column is accepted
-- and gives every row a window of one row, silently; n_in_window is the check.
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
-- The bounds are data, not literals: change variables.lo/hi and this changes.
SELECT r.variable,
       count(*)                    AS n_readings,
       count(DISTINCT r.sensor_id) AS n_sensors,
       min(r.value)                AS min_value,
       max(r.value)                AS max_value
FROM readings r
JOIN variables v ON v.variable = r.variable
WHERE r.value NOT BETWEEN v.lo AND v.hi
GROUP BY r.variable
ORDER BY n_readings DESC;


-- name: d2_impossible_temperature_on_a_healthy_battery
-- The four exceptions to "every impossible temperature came from a dying mote".
SELECT w.sensor_id, w.ts, w.temperature, w.voltage
FROM readings_wide w
JOIN variables v ON v.variable = 'temperature'
WHERE w.temperature NOT BETWEEN v.lo AND v.hi
  AND w.voltage >= 2.4
ORDER BY w.ts;



-- name: e_channel_extremes
-- Do the declared bounds actually get tested?  light's never fires.
SELECT r.variable, v.lo, v.hi,
       min(r.value) AS observed_min,
       max(r.value) AS observed_max,
       count(*)     AS n
FROM readings r
JOIN variables v ON v.variable = r.variable
GROUP BY r.variable
ORDER BY r.variable;


-- name: f_sentinel_values
-- -38.4 repeats exactly, which is what a sensor floor or an error code looks
-- like, not what noise looks like.  The same question asked of every channel.
SELECT r.variable, r.value, count(*) AS n, count(DISTINCT r.sensor_id) AS n_sensors
FROM readings r
JOIN variables v ON v.variable = r.variable
WHERE r.value NOT BETWEEN v.lo AND v.hi
GROUP BY r.variable, r.value
HAVING count(*) > 100
ORDER BY n DESC
LIMIT 10;


-- name: g_sampling_interval
-- Query (b) assumes a 31 s nominal period.  This is that assumption checked
-- against the data instead of taken from the assignment page.
WITH s AS (
    SELECT sensor_id,
           ts_unix - lag(ts_unix) OVER (PARTITION BY sensor_id ORDER BY ts_unix) AS gap
    FROM readings
    WHERE variable = 'voltage'
)
SELECT CAST(gap AS INT) AS gap_seconds, count(*) AS n
FROM s
WHERE gap IS NOT NULL AND gap < 120
GROUP BY gap_seconds
ORDER BY n DESC
LIMIT 10;

-- name: h_channels_never_reported
-- The three-way query GROUPs the readings and returns 53 motes, not 54.  Only
-- the roster can say which one is missing, and CROSS JOIN variables asks the
-- same question of every channel at once.
SELECT s.sensor_id, v.variable, count(r.value) AS n
FROM sensors s
CROSS JOIN variables v
LEFT JOIN readings r ON r.sensor_id = s.sensor_id AND r.variable = v.variable
GROUP BY s.sensor_id, v.variable
HAVING count(r.value) = 0
ORDER BY s.sensor_id, v.variable;