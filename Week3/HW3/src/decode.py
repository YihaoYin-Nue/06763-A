"""Stage 1: decode the raw NDJSON into one row per reading, lazily.

The whole stage is one Polars query plan: scan, filter to telemetry, pull the
message fields out of the payload struct, explode the metrics list, unnest the
metric struct, parse the three clocks, cast. It is collected exactly once.
"""

from __future__ import annotations

import polars as pl

from contract import RAW_GLOB, RESULTS, ROOT, TELEMETRY

STAMP = "%Y-%m-%dT%H:%M:%S%.3fZ"
TIDY_COLUMNS = ["tag", "value", "status", "event_time", "server_time", "received_at",
                "seq", "is_historical"]


def utc(expr: pl.Expr) -> pl.Expr:
    return expr.str.to_datetime(STAMP, time_unit="ms", time_zone="UTC", strict=True)


def plan() -> pl.LazyFrame:
    payload = pl.col("payload")
    metric = pl.col("metric")
    return (
        # Birth and telemetry payloads have different fields; reading every line
        # to infer the schema keeps the fields that first appear late.
        pl.scan_ndjson(str(ROOT / RAW_GLOB), infer_schema_length=None)
        .with_row_index("line")
        .filter(pl.col("topic") == TELEMETRY)
        .select(
            "line",
            pl.col("receivedAt").alias("received_raw"),
            payload.struct.field("seq").alias("seq"),
            payload.struct.field("timestamp").alias("server_raw"),
            payload.struct.field("isHistorical").alias("is_historical"),
            payload.struct.field("metrics").alias("metric"),
        )
        .explode("metric", empty_as_null=True)
        .select(
            "line",
            metric.struct.field("name").alias("tag"),
            metric.struct.field("value").cast(pl.Float64).alias("value"),
            metric.struct.field("statusCode").alias("status"),
            utc(metric.struct.field("sourceTimestamp")).alias("event_time"),
            utc(pl.col("server_raw")).alias("server_time"),
            utc(pl.col("received_raw")).alias("received_at"),
            pl.col("seq").cast(pl.Int64),
            pl.col("is_historical").cast(pl.Boolean),
        )
    )


def decode(show_plan: bool = True) -> pl.DataFrame:
    """Every reading in the capture, in arrival order, with its line number."""
    query = plan()
    explained = query.explain()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "explain.txt").write_text(explained + "\n", encoding="utf-8")
    if show_plan:
        print("optimised plan:\n" + explained + "\n")
    return query.collect()


if __name__ == "__main__":
    frame = decode()
    print(frame.head())
    print(frame.schema)
