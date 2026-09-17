from __future__ import annotations

import json

import pandas as pd

from contract import PROCESSED, RESULTS, Contract, contract
from decode import TIDY_COLUMNS, decode
from schema import build_schema, collection_window, failures

KEY = ["tag", "event_time"]


def clean(readings: pd.DataFrame, birth: dict, window) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Quarantine, deduplicate, validate. Returns (tidy, quarantine, counts);
    tidy still carries `line` (arrival order) for the lateness stage."""
    # 1. The device's verdict, one delivery at a time. A republished analyser
    #    value can arrive Good in one message and Uncertain or Bad in another.
    flagged = readings["status"] != "Good"
    by_status = readings[flagged].assign(reason="status: " + readings.loc[flagged, "status"])
    good = readings[~flagged]

    # 2. One row per measurement: what was measured, and when. seq is one byte
    #    and wraps every 256 messages, so it is not part of the key. Good
    #    deliveries of one key carry the same value; the first arrival is kept.
    measurements = good.drop_duplicates(KEY, keep="first")

    # 3. The schema, every check at once.
    schema = build_schema(birth, window)
    reasons, cases = failures(schema, measurements[TIDY_COLUMNS])
    if len(cases) and cases["index"].isna().any():
        raise SystemExit("schema failures not tied to a row (a column is missing or mistyped):\n"
                         + cases[cases["index"].isna()].to_string())
    by_schema = measurements.loc[reasons.index].assign(reason="schema: " + reasons)
    tidy = measurements.drop(index=reasons.index)

    quarantine = pd.concat([by_status, by_schema])
    counts = {
        "messages": int(readings["line"].nunique()),
        "readings": len(readings),
        "status": readings["status"].value_counts().to_dict(),
        "good_deliveries": len(good),
        "duplicates_removed": len(good) - len(measurements),
        "good_measurements": len(measurements),
        "schema_failure_cases": cases["check"].value_counts().to_dict() if len(cases) else {},
        "schema_rejected": len(by_schema),
        "tidy_rows": len(tidy),
        "quarantine_rows": len(quarantine),
        "quarantine_reasons": quarantine["reason"].value_counts().to_dict(),
        "window_plant": [str(window[0]), str(window[1])],
    }
    return tidy, quarantine, counts


def write(tidy: pd.DataFrame, quarantine: pd.DataFrame, counts: dict) -> None:
    PROCESSED.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    (tidy[TIDY_COLUMNS].sort_values(["event_time", "tag"], kind="stable")
     .to_parquet(PROCESSED / "tidy.parquet", index=False))
    (quarantine[TIDY_COLUMNS + ["reason"]].sort_values(["event_time", "tag", "received_at"], kind="stable")
     .to_parquet(PROCESSED / "quarantine.parquet", index=False))
    (RESULTS / "clean_counts.json").write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")


def run(c: Contract, readings: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    tidy, quarantine, counts = clean(readings, c.birth, collection_window(c, readings["received_at"]))
    write(tidy, quarantine, counts)
    return tidy, quarantine, counts


def main() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    return run(contract(), decode(show_plan=False).to_pandas())


if __name__ == "__main__":
    _, _, counts = main()
    print(json.dumps(counts, indent=2))
