-- 06763 A2 -- Intel Berkeley Lab sensor data, SQLite.

PRAGMA foreign_keys = ON;   -- per connection, off by default, and a silent no-op inside a transaction

-- the 54 deployed motes from mote_locs.txt, which is not the list of motes that reported
CREATE TABLE sensors (
    sensor_id INTEGER PRIMARY KEY,
    x_m       REAL NOT NULL,
    y_m       REAL NOT NULL
) STRICT;

-- long format, one row per channel per transmission; an absent channel is an absent row
CREATE TABLE readings (
    sensor_id INTEGER NOT NULL REFERENCES sensors(sensor_id),
    ts        TEXT    NOT NULL,   -- 'YYYY-MM-DD HH:MM:SS.ffffff' UTC, fixed width so text sorts by time
    ts_unix   REAL    NOT NULL,   -- the same instant as a number, which is what a RANGE frame needs
    epoch     INTEGER NOT NULL,   -- the mote's own counter; it resets on reboot, so it is not part of the key
    variable  TEXT    NOT NULL CHECK (variable IN ('temperature', 'humidity', 'light', 'voltage')),
    value     REAL    NOT NULL
) STRICT;
