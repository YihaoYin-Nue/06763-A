# Report
Yihao Yin (yihaoyin)

## Part 1 Schema
### Results
|  | rows | columns |
|---|---:|---|
| sensors | 54 | sensor_id INTEGER PRIMARY KEY, x_m REAL, y_m REAL |
| readings | 9119212 | sensor_id INTEGER (FK), ts TEXT, ts_unix REAL, epoch INTEGER, variable TEXT (CHECK), value REAL |
### Discussion
sensors is loaded from mote_locs.txt but not from SELECT DISTINCT moteid, because those are different lists: only 53 of the 54 deployed motes ever transmitted. readings is long format, which makes it easy to select one channel and cheap to add another variable. One column per channel buys the opposite: the four channels of a reading sit side by side, so comparing a temperature against its own voltage is one WHERE rather than a self-join.
The cost is that every query pays for rows it does not want: query (a) needs temperature only but reads all rows to use only 1/4 of them. ts, ts_unix and epoch are each stored four times over.
SQLite has no date type, so the instant is stored twice. ts is fixed-width ISO-8601 in UTC and it sorts chronologically; ts_unix is a number because a RANGE frame needs one. Both tables are STRICT, so a value of the wrong type is rejected instead of being stored anyway.
The natural key is (sensor_id, ts, variable). I first tried (sensor_id, epoch, variable), which the dataset documentation calls monotonically increasing per mote, but it loses 448435 rows because motes restart the counter after a reboot.
PRAGMA foreign_keys = ON is issued wherever a connection is opened, because it is per-connection and off by default.

## Part 2 Load and Cleaning
### Results
| rule | rows |
|---|---:|
| in file | 2313682 |
| moteid empty, rejected | 526 |
| moteid outside the 1-54 roster, rejected | 9866 |
| unparseable timestamp or value | 0 |
| accepted | 2303290 |
| empty channel fields, recorded as absent | 93948 |
| readings written | 9119212 |
| foreign key violations | 0 |
Load time 22.2 s. Plausible bounds, applied at read time in query (d):
| channel | bound | basis | readings outside |
|---|---|---|---:|
| humidity | 0-100 %RH | dataset doc, "ranging from 0-100%" | 299084 |
| voltage | 2.0-3.0 V | dataset doc, lithium ion, "ranging from 2-3" | 2746 |
| temperature | 0-50 degC | general working environment | 408133 |
| light | 0-2000 lux | observed to saturate at 1847.36 lux | 0 |
### Discussion
Every row of data.txt has eight fields separated by exactly one space, and a channel that did not arrive leaves its field empty, so two spaces appear in a row. Splitting on a single space keeps that empty field and every row comes out with eight. Splitting on any amount of whitespace, which is what line.split() and pandas do by default, reads those two spaces as one separator, so 93879 rows lose a field and every value after the gap shifts one column left with no error raised. An empty field means the measurement is missing rather than zero, so the loader writes no row for it.
Readings outside these bounds are kept in the database, neither deleted nor marked. The bounds are written directly into query (d) and applied when it runs, so changing one gives a different answer without reloading anything.
1847.36 lux is the largest value in the file and repeats 139078 times, far more often than any other, so the sensors reach their maximum there and keep reporting the same number. Everything below that can be real, since some sensors sit near windows, so the bound is set at 2000 rather than lower.
The dying-battery anomalies are counted but not cleaned out. Temperatures outside 0-50 degC come with a voltage averaging 2.19 V in the same transmission, against 2.55 V for the rest. A voltage rule cannot be derived from the temperatures, since the ones that would set it are the corrupted readings themselves; and temperatures cannot be judged by the voltage, since the documentation says the voltage reading itself changes with temperature and over 200000 readings below 2.40 V carry normal temperatures. Dropping everything below some voltage would throw away good data to remove bad.

## Part 3 Indexing
### Method
The probe query counts the readings from sensor 22 on 2004-03-09. It is run once to warm the cache and then five more times, and the fastest of those five is reported. EXPLAIN QUERY PLAN reports no timings, so the timing comes from time.perf_counter() in Python. The index is created after the load rather than in schema.sql, which is why an unindexed table exists to measure. Both configurations return the same 10452 rows, so only the cost changed.
### Results
| index | best | EXPLAIN QUERY PLAN |
|---|---:|---|
| none | 397.97 ms | SCAN readings |
| (sensor_id, ts, variable) | 0.19 ms | SEARCH readings USING COVERING INDEX ux_readings (sensor_id=? AND ts>? AND ts<?) |
### Discussion
SCAN readings means SQLite reads every row in the table and keeps the ones that match, so a question about one mote on one day costs a walk over all 9119212 rows. SEARCH means it uses the index instead, a second copy of (sensor_id, ts, variable) kept in sorted order, so it can jump straight to that sensor and then to that day inside it. It reads about 10452 entries rather than the whole table, which is the difference between the two timings.
The part in parentheses says which conditions the index actually handled, and here it handled all three. That depends on column order: sensor_id comes first, so one mote's readings sit together and its day is a contiguous run inside them. An index built as (ts, sensor_id) would constrain only ts and read that day for all 54 motes, thirty-seven times as many rows. COVERING means every column the query needs is already in the index, so the table is never opened.

## Part 4 The Three-Way Comparison
### Method
All three engines answer the same question, the average temperature of each of the 53 motes that reported one. Only the query itself is timed, warm, best of 5. Lines of code counts the expression that produces the answer rather than the setup, so the pandas read_parquet call is not included.
### Results
| engine | before it can answer | query | lines of code |
|---|---|---:|---:|
| pandas, Parquet into RAM | 0.5 s read | 105.5 ms | 1 |
| SQLite, lab.db | 22.2 s load | 1019.9 ms | 2 |
| DuckDB, Parquet | none | 19.0 ms | 2 |
### Discussion
pandas and SQLite return exactly the same numbers, and DuckDB differs from them by at most 1.33e-12. That is a different order of addition rather than a different answer: pandas and SQLite accumulate row by row, DuckDB adds partial sums from vectorised chunks, and floating-point addition is not associative. DuckDB is also the fastest and needs nothing prepared, since it reads the Parquet tree in place, while SQLite is slowest because the long schema makes it walk the whole table.

## Part 5 When You Would Choose Each Store
A row store keeps one reading's fields next to each other on disk, and it reads in fixed-size pages, so a page holds whole rows. Averaging the value column means loading pages that carry every other column too, because value was never stored on its own. A column store writes all the values of one column together, so reading value reads only value. The Parquet tree is 95 MB against lab.db's 1038 MB.
SQLite is good at touching a small part of the data. Reading one sensor over one day takes a fraction of a millisecond because the index leads straight to those rows, and correcting a single reading is one UPDATE where the same fix in Parquet means rewriting the whole day's file. The file is also the data, so a value written a second ago is already in the next query. And it is the only one of the two that checks anything: the foreign key, STRICT and the CHECK reject a bad mote, a wrong type or an unknown channel at insert time.
I would keep the data in SQLite while it is still arriving or being corrected, and whenever a question names one mote or one day. I would go to Parquet and DuckDB once the data is finished and the questions change shape, scanning months but naming two or three columns. The deciding factor is the access pattern rather than the size of the data, and since the export takes 0.9 s there is no reason to pick only one.

## AI Use
Generative AI was used to fix bugs in the code, to resolve SQL syntax problems, and to improve the wording and organisation of this report; every number here was produced by the scripts in this repository on my own machine.
