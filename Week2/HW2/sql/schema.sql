-- 06763 A2 -- Intel Berkeley Lab sensor data, SQLite.
-- Indexes are in sql/indexes.sql, created after the bulk load.
-- Run outside a transaction: PRAGMA foreign_keys is a silent no-op inside one.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS readings;
DROP TABLE IF EXISTS readings_wide;
DROP TABLE IF EXISTS variables;
DROP TABLE IF EXISTS sensors;

CREATE TABLE sensors (
    sensor_id INTEGER PRIMARY KEY,
    x_m       REAL NOT NULL,
    y_m       REAL NOT NULL
) STRICT;

CREATE TABLE variables (
    variable  TEXT PRIMARY KEY,
    unit      TEXT NOT NULL,
    lo        REAL NOT NULL,
    hi        REAL NOT NULL,
    rationale TEXT NOT NULL
) STRICT;

CREATE TABLE readings (
    sensor_id INTEGER NOT NULL REFERENCES sensors(sensor_id),
    ts        TEXT    NOT NULL,   -- 'YYYY-MM-DD HH:MM:SS.ffffff', UTC
    ts_unix   REAL    NOT NULL,   -- same instant; a RANGE frame needs a number
    epoch     INTEGER NOT NULL,
    variable  TEXT    NOT NULL REFERENCES variables(variable),
    value     REAL    NOT NULL    -- an absent channel is an absent row
) STRICT;

CREATE TABLE readings_wide (
    sensor_id   INTEGER NOT NULL REFERENCES sensors(sensor_id),
    ts          TEXT    NOT NULL,
    ts_unix     REAL    NOT NULL,
    epoch       INTEGER NOT NULL,
    temperature REAL,
    humidity    REAL,
    light       REAL,
    voltage     REAL,
    PRIMARY KEY (sensor_id, ts)
) STRICT;

INSERT INTO variables (variable, unit, lo, hi, rationale) VALUES
  ('temperature', 'degC', 0.0,   50.0, 'indoor office; the SHT11 reaches 123.8 C, a Berkeley lab does not'),
  ('humidity',    '%RH',  0.0,  100.0, 'relative humidity is a percentage'),
  ('light',       'lux',  0.0, 2000.0, 'illuminance cannot be negative; the mote saturates near 1800 lux'),
  ('voltage',     'V',    2.0,    3.5, 'two AA cells');