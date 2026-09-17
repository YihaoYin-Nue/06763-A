from __future__ import annotations

import json

import pandas as pd

from clean import clean
from contract import RESULTS, Contract, contract
from decode import decode
from schema import collection_window

ALLOWANCES_MIN = [0, 15, 30, 45, 60, 90, 120, 180, 240]
WINDOW = "30min"
COMPARED = "Reactor Pressure"          # a birth description, not a tag name


def lateness(readings: pd.DataFrame, tidy: pd.DataFrame) -> pd.Series:
    """Plant minutes each tidy reading trailed the frontier in force when its
    message landed (the newest event_time in any earlier message); 0 if ahead."""
    # Arrival order is the line number. Several replayed messages can share one
    # receivedAt millisecond, so grouping on receivedAt (as L6 does) merges them.
    newest = readings.groupby("line")["event_time"].max().sort_index()
    before = newest.cummax().shift()
    behind = tidy["line"].map(before) - tidy["event_time"]
    return (behind.dt.total_seconds() / 60).clip(lower=0).fillna(0.0)


def watermark_sweep(tidy: pd.DataFrame, late_min: pd.Series, newest: pd.Timestamp,
                    acceleration: float) -> pd.DataFrame:
    rows = []
    for allowance in ALLOWANCES_MIN:
        dropped = int((late_min > allowance).sum())
        # Not final when collection stopped: the watermark (newest - allowance)
        # had not yet passed these event times.
        waiting = int((tidy["event_time"] > newest - pd.Timedelta(minutes=allowance)).sum())
        rows.append({
            "allowance_plant_min": allowance,
            "wall_clock_s": round(allowance * 60 / acceleration, 2),
            "completeness_pct": round(100 * (1 - dropped / len(tidy)), 3),
            "dropped_as_late": dropped,
            "still_waiting": waiting,
        })
    return pd.DataFrame(rows)


def clock_comparison(tidy: pd.DataFrame, c: Contract) -> tuple[str, pd.DataFrame]:
    """The same 30-minute aggregates, bucketed by event time and by receivedAt
    converted to the plant clock."""
    tag = next(t["name"] for t in c.tags if t["description"] == COMPARED)
    frame = tidy.assign(received_plant=c.plant_time(tidy["received_at"]))

    def per_window(clock: str) -> dict[str, pd.Series]:
        indexed = frame.set_index(clock).sort_index()
        return {
            "readings": indexed["value"].resample(WINDOW).size(),
            "historical": indexed["is_historical"].resample(WINDOW).sum(),
            "mean": indexed.loc[indexed["tag"] == tag, "value"].resample(WINDOW).mean(),
        }

    by_event, by_received = per_window("event_time"), per_window("received_plant")
    out = pd.concat({
        "readings_by_event_time": by_event["readings"],
        "readings_by_received_at": by_received["readings"],
        "historical_by_received_at": by_received["historical"],
        "mean_by_event_time": by_event["mean"],
        "mean_by_received_at": by_received["mean"],
    }, axis=1)
    counts = ["readings_by_event_time", "readings_by_received_at", "historical_by_received_at"]
    out[counts] = out[counts].fillna(0).astype(int)
    out["readings_diff"] = out["readings_by_received_at"] - out["readings_by_event_time"]
    out["mean_diff"] = out["mean_by_received_at"] - out["mean_by_event_time"]
    out.index.name = "window_start"
    return tag, out.reset_index()


def summarise(readings, tidy, late_min, c, tag, clocks) -> dict:
    late = late_min > 0
    analyser = tidy["tag"].isin(c.analysers)

    def spread(mask):
        values = late_min[mask & late]
        return {"late": int(values.size), "of": int(mask.sum()),
                "median_min": float(values.median()) if values.size else 0.0,
                "p95_min": float(values.quantile(0.95)) if values.size else 0.0,
                "max_min": float(values.max()) if values.size else 0.0}

    worst_n = clocks.loc[clocks["readings_diff"].abs().idxmax()]
    worst_m = clocks.loc[clocks["mean_diff"].abs().idxmax()]
    std = float(tidy.loc[tidy["tag"] == tag, "value"].std())
    return {
        "measurements": len(tidy),
        "late_pct": round(100 * float(late.mean()), 2),
        "lateness_all": spread(pd.Series(True, index=tidy.index)),
        "lateness_live": spread(~tidy["is_historical"]),
        "lateness_historical": spread(tidy["is_historical"]),
        "lateness_continuous": spread(~analyser),
        "lateness_analyser": spread(analyser),
        "newest_event_time": str(readings["event_time"].max()),
        "clock_comparison": {
            "tag": tag,
            "tag_std": std,
            "windows": len(clocks),
            "max_abs_readings_diff": int(abs(worst_n["readings_diff"])),
            "at_window": str(worst_n["window_start"]),
            "readings_there": {"event_time": int(worst_n["readings_by_event_time"]),
                               "received_at": int(worst_n["readings_by_received_at"]),
                               "historical_by_received_at": int(worst_n["historical_by_received_at"])},
            "max_abs_mean_diff": float(abs(worst_m["mean_diff"])),
            "mean_diff_at_window": str(worst_m["window_start"]),
            "windows_with_mean_only_one_way": int(clocks[["mean_by_event_time", "mean_by_received_at"]]
                                                  .isna().sum(axis=1).eq(1).sum()),
        },
    }


def run(c: Contract, readings: pd.DataFrame, tidy: pd.DataFrame) -> dict:
    late_min = lateness(readings, tidy)
    sweep = watermark_sweep(tidy, late_min, readings["event_time"].max(), c.acceleration)
    tag, clocks = clock_comparison(tidy, c)
    summary = summarise(readings, tidy, late_min, c, tag, clocks)

    RESULTS.mkdir(exist_ok=True)
    sweep.to_csv(RESULTS / "watermark.csv", index=False)
    clocks.to_csv(RESULTS / "clock_comparison.csv", index=False)
    (RESULTS / "stream_counts.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return {"sweep": sweep, "clocks": clocks, "summary": summary}


def main() -> dict:
    c = contract()
    readings = decode(show_plan=False).to_pandas()
    tidy, _, _ = clean(readings, c.birth, collection_window(c, readings["received_at"]))
    return run(c, readings, tidy)


if __name__ == "__main__":
    out = main()
    print(out["sweep"].to_string(index=False), "\n")
    print(json.dumps(out["summary"], indent=2))
