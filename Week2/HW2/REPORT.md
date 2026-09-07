# A2 Sensor Data into SQLite and DuckDB

Yihao Yin (yihaoyin)

## Part 1 Schema

### Results

|  | rows | columns |
|---|---:|---|
| sensors | 54 | sensor_id, x_m, y_m |
| readings | 9,119,212 | sensor_id, ts, ts_unix, epoch, variable (CHECK), value |

I tested two kinds natural key to check if they match the 9,119,212 loaded rows:

| candidate | distinct | unique |
|---|---:|---|
| (sensor_id, ts, variable) | 9,119,212 | yes |
| (sensor_id, epoch, variable) | 8,670,777 | no, 448,435 collisions |

### Discussion

sensors is loaded from mote_locs.txt but not from SELECT DISTINCT moteid, because those are different lists: only 53 of the 54 deployed motes ever transmitted. readings is long format, so the channel name is a value in the variable column rather than a column name, which makes it easy to select one channel and cheap to add another variable. 

The cost is that every query pays for rows it does not want: query (a) needs temperature only but reads all rows to use only 1/4 of them. ts, ts_unix and epoch are each stored for four times. 

SQLite has no date type, so the instant is stored twice. ts is fixed-width ISO-8601 in UTC and it sorts chronologically; ts_unix is a number because a RANGE frame needs one. 

The natural key is (sensor_id, ts, variable). I first tried (sensor_id, epoch, variable), since the dataset documentation calls epoch a monotonically increasing sequence number from each mote, but it turned out to lose 448,435 rows. The reason is that motes will restart the counter after reboot. To solve this, I chose (sensor_id, ts, variable) as the natural key.

PRAGMA foreign_keys = ON is issued wherever a connection is opened, because it is per-connection and off by default.

## Part 2 Load and Cleaning

### Results

| rule | rows |
|---|---:|
| in file | 2,313,682 |
| moteid empty, rejected | 526 |
| moteid outside the 1-54 roster, rejected | 9,866 |
| unparseable timestamp or value | 0 |
| accepted | 2,303,290 |
| empty channel fields, recorded as absent | 93,948 |
| readings written | 9,119,212 |
| foreign key violations | 0 |

Load time 22.2 s. Plausible bounds, applied at read time in query (d):

| channel | bound | basis | readings outside |
|---|---|---|---:|
| humidity | 0-100 %RH | dataset doc, "ranging from 0-100%" | 299,084 |
| voltage | 2.0-3.0 V | dataset doc, lithium ion, "ranging from 2-3" | 2,746 |
| temperature | 0-50 °C | general working environment | 408,133 |
| light | 0-2000 lux | observed to saturate at 1847.36 lux | 0 |

### Discussion

data.txt is split on a single space. Under that rule all 2,313,682 rows yield exactly eight fields, so no row in this file is malformed. Splitting on runs of whitespace instead makes 93,879 rows look ragged and shifts every value after the gap one column left, producing rows that are well-formed, plausible and wrong. The 526 rows whose fields after epoch are all empty end in whitespace, so .strip() would move them out of the empty-moteid count and into a malformed one. An empty channel field is an absent measurement and not a zero one, so in the long table it is the absence of a row. Four rows carry a whole-second timestamp, and the loader pads the fraction to six digits so ts stays fixed width.

Implausible readings are neither deleted nor flagged. The bounds are literals in query (d), so changing one changes the answer and no data is destroyed. The four are not equally strong: humidity and voltage come from the dataset documentation, light from the observed hardware ceiling, and temperature only from the deployment being an indoor office. Temperature is therefore the one I would expect to be argued with.

Light never fires, so query (d) returns three channels rather than four. That is a result and not an omission: the observed range is 0 → 1847.36 lux, and 1847.36 appears 139,078 times, eight times more often than any other value above 1000, which is what a saturated sensor looks like.

Impossible temperatures correlate with low voltage in the same transmission, mean 2.19 V against 2.55 V. The relation runs one way only: over 200,000 readings between 2.20 and 2.40 V carry perfectly normal temperatures, and the documentation notes that the voltage reading itself varies with temperature. Cause is not established, so nothing was cleaned on this basis.

Query (b) counts distinct timestamps per mote against the observation window divided by a 30 s nominal period. That period is an assumption, but n_expected is identical for every mote, so it scales the column without changing the ranking. The real weakness is that it charges every mote for the whole window, so died-early and never-started score alike. Mote 5, with 35 samples in 36 days, tops the list either way, and is invisible to any query starting FROM readings.

## Part 3 Indexing

### Results

Probe: count(*) for sensor 22 over 2004-03-09, best of 5 warm runs, timed with time.perf_counter() because EXPLAIN QUERY PLAN reports no timings. Both configurations return the same 10,452 rows.

| index | best | EXPLAIN QUERY PLAN |
|---|---:|---|
| none | 397.97 ms | SCAN readings |
| (sensor_id, ts, variable) | 0.19 ms | SEARCH readings USING COVERING INDEX ux_readings (sensor_id=? AND ts>? AND ts<?) |

### Discussion

The parenthesis in the plan lists the columns the index actually constrained, here all three predicates. Leading with sensor_id puts one mote's day in one contiguous run. An index leading with ts would also be used, but would constrain only ts and read that whole day for all 54 motes: 386,187 rows to answer a question about 10,452, thirty-seven times as many. Column order is what decides how much of a composite index a query can use. COVERING means the answer came out of the index without the table being read at all, which is why 0.19 ms is possible.

The index is also the natural key, so one statement buys both the uniqueness constraint and the access path. It is built after the bulk load so that 9.1M inserts need not maintain it, and so that an unindexed state exists to measure.

## Part 4 The Three-Way Comparison

### Results

Question: average temperature per mote over the whole history. Timed: the query only, warm, best of 5. The setup column is what each engine needs from data.txt before it can answer at all.

| engine | before it can answer | query | lines of code |
|---|---|---:|---:|
| pandas, Parquet into RAM | 0.1 s read | 101.1 ms | 1 |
| SQLite, lab.db | 22.2 s load | 1,046.9 ms | 2 |
| DuckDB, Parquet | none | 20.4 ms | 2 |

### Discussion

DuckDB is 51 times faster than SQLite and needs nothing prepared, since it reads the Parquet tree in place. SQLite is slowest because the long schema makes it walk all 9,119,212 rows, three quarters of which belong to channels the question never names. The pandas setup number is not comparable to the other two: 0.1 s is a warm read of three columns, and the approach works only while the answer fits in RAM.

## Part 5 When You Would Choose Each Store

### Discussion

A row store keeps a reading's columns contiguous, so averaging one channel still walks every page: the bytes it wants are interleaved with the bytes it does not. The long schema quadruples that, since three of every four rows read belong to a channel the question never mentions. Parquet stores each column separately and the whole tree is 95 MB against lab.db's 1,038 MB, so DuckDB reads a fraction of the bytes SQLite must. That is what OLAP means here, a wide scan of few columns over the whole history.

SQLite wins elsewhere. One sensor over one day is 0.19 ms, against a file that is always current with no export step between the writer and the reader. Constraints are enforced on write, so a reading cannot name a mote outside the roster, store text in a REAL column, or invent a fifth channel, and foreign_key_check proves it afterwards. Correcting a single reading is an UPDATE, while in Parquet it is rewriting a partition. It is one file, with no server and no daemon. That is the OLTP side: many small correct writes, point lookups, integrity.

The export takes 0.9 s for 9,119,212 rows, so running both is not a compromise.

## AI Use

Generative AI (Claude) was used throughout: to profile the raw file, to draft the loader, the SQL and this report, and as a reviewer of the schema and index decisions. Every number here was produced by the scripts in this repository on my own machine and checked against the counts published on the assignment page.
