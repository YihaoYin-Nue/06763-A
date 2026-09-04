CREATE UNIQUE INDEX ux_readings_natural ON readings (sensor_id, ts, variable);
CREATE INDEX        ix_wide_sensor_time ON readings_wide (sensor_id, ts_unix);