from __future__ import annotations

import json

import pandas as pd

from clean import clean
from contract import PROCESSED, RESULTS, Contract, contract
from decode import decode
from schema import collection_window


def spine(tidy: pd.DataFrame, c: Contract) -> pd.DatetimeIndex:
    """Every sample instant from the first continuous sample to the last.

    Starting at the earliest event_time overall would start at a lagged
    analyser timestamp, before the first message, where no continuous
    instrument can have a value."""
    times = tidy.loc[tidy["tag"].isin(c.continuous), "event_time"]
    return pd.date_range(times.min(), times.max(), freq=pd.Timedelta(seconds=c.sample_seconds),
                         unit="ms", name="event_time")


def widen(tidy: pd.DataFrame, c: Contract, instants: pd.DatetimeIndex) -> pd.DataFrame:
    """Spine first, then the long table pivoted onto it: one row per instant,
    one column per birth tag, a hole wherever nothing measured survived."""
    wide = tidy.pivot(index="event_time", columns="tag", values="value")
    grid = pd.DataFrame(index=instants).join(wide, how="left")
    return grid.reindex(columns=c.names)     # a tag that never reported still gets a column


def schedules(readings: pd.DataFrame, c: Contract, start: pd.Timestamp) -> pd.DataFrame:
    """Each analyser's analysis instants, read off the event times it reported:
    a period in samples and a phase relative to the grid start."""
    rows = []
    step = pd.Timedelta(seconds=c.sample_seconds)
    for t in c.tags:
        if t.get("analyserIntervalHours") is None:
            continue
        period = round(t["analyserIntervalHours"] * 3600 / c.sample_seconds)
        times = readings.loc[readings["tag"] == t["name"], "event_time"].drop_duplicates()
        phases = (((times - start) / step).round().astype(int) % period).value_counts()
        rows.append({"tag": t["name"], "period": period, "phase": int(phases.index[0]),
                     "off_phase_times": int(phases.iloc[1:].sum())})
    return pd.DataFrame(rows).set_index("tag")


def empty_cells(grid: pd.DataFrame, readings: pd.DataFrame, quarantine: pd.DataFrame,
                c: Contract) -> pd.DataFrame:
    """One row per empty cell before filling, with the reason it is empty."""
    sched = schedules(readings, c, grid.index[0])
    cells = grid.isna().rename_axis(columns="tag").stack()
    cells = cells[cells].reset_index()[["event_time", "tag"]]
    cells["row"] = grid.index.get_indexer(cells["event_time"])
    cells["tag_class"] = cells["tag"].map(lambda t: "analyser" if t in sched.index else "continuous")

    delivered = pd.MultiIndex.from_frame(readings[["tag", "event_time"]])
    held = pd.MultiIndex.from_frame(quarantine[["tag", "event_time"]])
    sampled = set(readings.loc[readings["tag"].isin(c.continuous), "event_time"])
    key = pd.MultiIndex.from_frame(cells[["tag", "event_time"]])

    period = cells["tag"].map(sched["period"])
    phase = cells["tag"].map(sched["phase"])
    on_schedule = cells["tag_class"].eq("continuous") | ((cells["row"] - phase) % period == 0)
    first_value = grid.notna().idxmax().where(grid.notna().any())
    leading = cells["event_time"] < cells["tag"].map(first_value)

    cause = pd.Series("other", index=cells.index)
    cause[~key.isin(delivered)] = "reading never delivered"
    cause[~cells["event_time"].isin(sampled)] = "sample never delivered (lost message)"
    cause[~on_schedule] = "between analyses"
    cause[key.isin(held)] = "quarantined"
    cells["cause"] = cause
    cells["leading"] = leading.to_numpy()
    return cells


def fill(grid: pd.DataFrame, tidy: pd.DataFrame, c: Contract) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Fill every hole. Returns (filled grid, mask of the cells that were empty, counts)."""
    mask = grid.isna()
    filled = grid.copy()

    # Continuous instruments: every hole is one missing sample between two
    # measured ones, so interpolate in time. Only an edge, which has a
    # neighbour on one side only, takes the nearest measured value.
    inside = filled[c.continuous].interpolate(method="time", limit_area="inside")
    filled[c.continuous] = inside.ffill().bfill()

    # Analysers: hold the last analysis until the next one, as the plant does.
    # Interpolating would use an analysis that had not happened yet. The first
    # row is seeded with the last analysis before the grid starts, if any, so
    # the leading holes are carried forward rather than filled from the future.
    analysers = filled[c.analysers]
    earlier = tidy[tidy["tag"].isin(c.analysers) & (tidy["event_time"] < grid.index[0])]
    seed = earlier.sort_values("event_time").groupby("tag")["value"].last()
    first = analysers.iloc[0].isna()
    analysers.iloc[0] = analysers.iloc[0].fillna(seed)
    held = analysers.ffill()
    filled[c.analysers] = held.bfill()      # only a tag with no earlier value at all

    counts = {
        "continuous_interpolated": int((mask[c.continuous] & inside.notna()).sum().sum()),
        "continuous_edge_nearest": int((mask[c.continuous] & inside.isna()).sum().sum()),
        "analyser_seeded_from_before_grid": int((first & analysers.iloc[0].notna()).sum()),
        "analyser_held_forward": int((mask[c.analysers] & held.notna()).sum().sum()),
        "analyser_filled_backward": int((mask[c.analysers] & held.isna()).sum().sum()),
        "still_empty": int(filled.isna().sum().sum()),
    }
    return filled, mask, counts


def run(c: Contract, readings: pd.DataFrame, tidy: pd.DataFrame, quarantine: pd.DataFrame) -> dict:
    instants = spine(tidy, c)
    grid = widen(tidy, c, instants)
    off_grid = tidy[~tidy["event_time"].isin(instants)]
    cells = empty_cells(grid, readings, quarantine, c)
    filled, mask, fill_counts = fill(grid, tidy, c)

    summary = {"rows": len(grid), "start": str(instants[0]), "end": str(instants[-1]),
               "tidy_rows_off_grid": len(off_grid),
               "off_grid_before_start": int((off_grid["event_time"] < instants[0]).sum()),
               "fill": fill_counts}
    by_class = {}
    for label, tags in (("continuous", c.continuous), ("analyser", c.analysers)):
        total = len(grid) * len(tags)
        empty = int(grid[tags].isna().sum().sum())
        by_class[label] = {"tags": len(tags), "cells": total, "empty": empty,
                           "pct": round(100 * empty / total, 2)}
    summary["empty_before_fill"] = by_class

    causes = (cells.groupby(["tag_class", "cause"]).agg(cells=("tag", "size"), leading=("leading", "sum"))
              .reset_index())

    # All value columns first, then all masks: the grader pairs a mask with a
    # tag by prefix, so XMEAS_1 must meet XMEAS_1_filled before XMEAS_10_filled.
    table = pd.concat([filled, mask.add_suffix("_filled")], axis=1).reset_index()
    PROCESSED.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    table.to_parquet(PROCESSED / "grid.parquet", index=False)
    causes.to_csv(RESULTS / "empty_cells.csv", index=False)
    (RESULTS / "grid_counts.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return {"grid": grid, "filled": filled, "mask": mask, "cells": cells, "causes": causes,
            "summary": summary, "schedules": schedules(readings, c, instants[0]),
            "readings": readings, "quarantine": quarantine, "tidy": tidy, "contract": c}


def main() -> dict:
    c = contract()
    readings = decode(show_plan=False).to_pandas()
    tidy, quarantine, _ = clean(readings, c.birth, collection_window(c, readings["received_at"]))
    return run(c, readings, tidy, quarantine)


if __name__ == "__main__":
    out = main()
    print(json.dumps(out["summary"], indent=2))
    print("\n" + out["causes"].to_string(index=False))
