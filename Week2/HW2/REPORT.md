# A2 -- Intel Berkeley Lab sensor data in SQLite and DuckDB

Yihao Yin (yihaoyin)

## 1. Schema

Two dimensions and one long fact table, every table STRICT.

`sensors` (54 rows) comes from `mote_locs.txt`, not from the motes that appear in
`data.txt`. Those are different lists, and query (b) turns on the difference.
`variables` (4 rows) holds each channel's unit and its plausible bounds, so the
bounds are data rather than literals inside a query; query (d) joins to them.
`readings` is long -- `(sensor_id, ts, ts_unix, epoch, variable, value)`, 9,119,212
rows, foreign keys to both dimensions. `readings_wide` is the typed shape, one row
per transmission and one column per channel, 2,303,290 rows.

**The trade.** Long is the fact table because a new channel needs no DDL and because
query (d) applies four different bounds in one predicate. The cost is real: `ts`,
`ts_unix` and `epoch` are stored four times over, and "the channel did not arrive" is
the absence of a row rather than a NULL, which is harder to ask about. The wide table
is kept for where the trade goes the other way: query `d2` compares a temperature
against the voltage from the same transmission, which is one row in the wide table
and a self-join over two 2.3M-row scans in the long one.

**Time.** SQLite has no date type, so the instant is stored twice. `ts` is fixed-width
ISO-8601 text in UTC, so lexicographic order is chronological and `strftime` works on
it. `ts_unix` is a number because a RANGE frame needs one: ordering a RANGE frame by
the text column is accepted and silently gives every row a window of one row, so
query (c) prints `count(*) OVER w` beside the average. It runs 1..518, not 1.

`PRAGMA foreign_keys = ON` is issued in the one function that opens connections, and
`PRAGMA foreign_key_check` returns 0 rows. Indexes are not in `schema.sql`; they are
created after the load, for the reason in section 3.

## 2. Load and cleaning

`data.txt` is split on a single space. All 2,313,682 rows yield exactly eight fields.

| rule | effect |
|---|---|
| moteid empty | 526 rows rejected |
| moteid outside the 1-54 roster | 9,866 rows rejected |
| accepted | 2,303,290 rows |
| duplicate `(sensor_id, ts)` | 0 |
| channel fields that arrived empty | 93,948, recorded as absent |
| readings written to the long table | 9,119,212 |
| rows violating a foreign key | 0 |

An empty channel field is an absent measurement and not a zero one: in the long table
it is the absence of a row, in the wide table a NULL. Of the 93,948 empty channel
fields, 93,205 are the light channel, and voltage is never one of them.

**Implausible readings are neither deleted nor given a flag column.** The bounds live
in `variables.lo/hi` and query (d) applies them at read time. A materialised flag
would go stale the moment a bound changed; this cannot.

408,133 temperature readings fall outside 0-50 C, 17.7% of the channel, and 408,129
of them come from a transmission below 2.4 V. The four exceptions sit at 2.424-2.454
V, so 2.4 V is a sharp boundary but not a physical cliff. They are also not noise:
370,196 of the 408,133 are the single value 122.153 C on 51 of the 54 motes, and
humidity -3.91901 appears 212,232 times. Those are stuck sensor registers. Line 1 of
`data.txt` carries both.

Mote 5 transmitted 35 times in 36 days, every time carrying voltage and nothing else.
That is why `GROUP BY sensor_id` over `readings` returns 53 motes -- the same lesson
as query (b), that only the roster knows how many motes exist. The roster rule also
moved the end of the window from 2004-04-05 to 2004-04-03; the last two days of the
file hold only rows it rejects.

The sampling period is measured, not assumed: query `g` puts the modal gap at 30 s
with a secondary peak at 60 s, one missed round. Query (b) uses 30 s. Its weakness is
that it charges every mote for the whole window, so "died early" and "never started"
score alike.

## 3. Indexing

Probe: one sensor over one day, `count(*)`, median of 5 warm runs, sensor 22 on
2004-03-09. All three configurations return the same 10,452 rows.

| index | median | EXPLAIN QUERY PLAN |
|---|---:|---|
| none | 367.20 ms | `SCAN readings` |
| `(ts, sensor_id)` | 14.96 ms | `SEARCH readings USING COVERING INDEX ix_probe_ts_first (ts>? AND ts<?)` |
| `(sensor_id, ts, variable)` | 0.32 ms | `SEARCH readings USING COVERING INDEX ux_readings_natural (sensor_id=? AND ts>? AND ts<?)` |

The middle row is the point. Both indexes are used and they differ by 47x. The
parenthesis in the plan says why: it lists the columns the index actually
constrained. Leading with `ts` constrains only `ts`, so the query reads that day for
all 54 motes -- 386,187 rows against the 10,452 the answer needs. 37x the rows, 47x
the time. Leading with `sensor_id` puts one mote's day in one contiguous run.
`COVERING` means the answer came out of the index without the table being touched.

That index is also the natural key, so one statement buys both the uniqueness
constraint and the access path. Building it after the bulk load is faster than
maintaining it across 9.1M inserts, and it is what leaves an unindexed state to
measure. Cost: `lab.db` grows from 910 MB to 1,376 MB. 0.32 ms is bought with 51%
more disk.

## 4. The three-way comparison

Question: average temperature per mote over the whole history. Timed: the query only,
warm, best of 5. "Before it can answer" is what each engine needs from `data.txt`
first.

| engine | before it can answer | query | lines of code |
|---|---|---:|---:|
| pandas, Parquet into RAM | 0.4 s read | 99.1 ms | 1 |
| SQLite, `lab.db` | 10.5 s load | 628.5 ms | 2 |
| DuckDB, Parquet | none | 18.1 ms | 2 |

On disk, `lab.db` is 1,376 MB with its indexes and 910 MB without; the Parquet tree is
202 MB in 36 date partitions and took 1.0 s to write.

The answers agree. Query (a) computed from Parquet and from `lab.db`, joined across
all 36,082 hourly buckets: largest difference 0.0. On the per-mote averages, pandas
and SQLite agree exactly and DuckDB differs by at most 1.31e-12, because
floating-point addition is not associative and DuckDB combines partial sums from
vectorised chunks instead of accumulating in row order. At roughly 44,000 readings
per mote, eps * sqrt(n) * 20 C is about 9e-13. That is rounding, not disagreement.

## 5. When you would choose each store

A row store keeps a reading's six columns contiguous, so `avg(value)` over one
channel still walks every page: the bytes it wants are interleaved with the bytes it
does not. Parquet stores each column on its own, and the three columns this question
names are 11 MB of the 202 MB tree -- 5% of the bytes. That is the 34x gap between
DuckDB and SQLite, and it is what OLAP means here: a wide scan of a few columns over
the whole history.

The other half is what SQLite is better at, which is not nothing: one sensor over one
day is 0.32 ms against a file that is always current, with no export step between the
writer and the reader. Constraints are enforced on write, so a reading cannot name a
mote outside the roster and `foreign_key_check` proves it afterwards. Correcting a
single reading is an UPDATE; in Parquet it is rewriting a partition. The database is
one file, no server and no daemon. That is the OLTP side: many small correct writes,
point lookups, integrity.

The seam is cheap -- 1.0 s to export 9,119,212 rows -- so running both is not a
compromise, it is the design.

## AI use

Generative AI (Claude) was used throughout: to draft the loader, the SQL and this
report, to read the assignment's evidence script, and as a reviewer of the schema and
index design. Every number here was produced by the scripts in this repository and
checked against the counts published on the assignment page.
