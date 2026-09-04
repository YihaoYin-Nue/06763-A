Query: average temperature per mote, over all 53 motes.
Timed: the query only, warm, best of 5. 'Before it can answer' is what each engine needs from data.txt first.

| engine | before it can answer | query (ms) | lines of code |
|---|---|---:|---:|
| pandas (Parquet -> RAM) | 0.1 s read | 97.5 | 1 |
| SQLite (lab.db) | 10.1 s load | 610.7 | 2 |
| DuckDB (Parquet) | none | 18.3 | 2 |

On disk: lab.db 1,376 MB (indexes included), Parquet 201 MB.
The 3 columns this question names are 11 MB of that 201 MB, so the column store reads 5% of the bytes the row store must.

Largest disagreement in the answer: pandas vs SQLite 0, pandas vs DuckDB 1.17e-12.
