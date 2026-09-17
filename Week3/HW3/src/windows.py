from __future__ import annotations

import pandas as pd

from contract import PROCESSED, RESULTS, Contract, contract

WINDOW = pd.Timedelta(minutes=30)


def read_grid(c: Contract) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The filled grid and its fill mask, both indexed by event_time."""
    table = pd.read_parquet(PROCESSED / "grid.parquet").set_index("event_time")
    values = table[c.names]
    mask = table[[f"{name}_filled" for name in c.names]].set_axis(c.names, axis=1)
    return values, mask


def tumbling(values: pd.DataFrame, mask: pd.DataFrame, c: Contract) -> pd.DataFrame:
    """Tumbling 30-plant-minute windows in event time, labelled by their start,
    one row per window and one column per tag."""
    full = int(WINDOW / pd.Timedelta(seconds=c.sample_seconds))
    means = values.resample(WINDOW).mean()
    n = values.resample(WINDOW).size()
    invented = mask.resample(WINDOW).sum().sum(axis=1)
    info = pd.DataFrame({
        "n_samples": n,
        # A window holding fewer than `full` samples is kept, but flagged: its
        # mean is over a shorter stretch and is not comparable with the rest.
        "complete": n == full,
        "filled_pct": (100 * invented / (n * len(c.names))).round(3),
    })
    out = pd.concat([info, means], axis=1)
    out.index.name = "window_start"
    return out.reset_index()


def main() -> pd.DataFrame:
    c = contract()
    values, mask = read_grid(c)
    windows = tumbling(values, mask, c)
    RESULTS.mkdir(exist_ok=True)
    windows.to_csv(RESULTS / "windows.csv", index=False)
    return windows


if __name__ == "__main__":
    w = main()
    print(f"{len(w)} windows, {int((~w['complete']).sum())} incomplete")
    print(w.loc[~w["complete"], ["window_start", "n_samples", "filled_pct"]].to_string(index=False))
    print("\nfilled_pct over complete windows:", w.loc[w["complete"], "filled_pct"].describe().round(2).to_dict())
    print(w.iloc[:3, :7].to_string(index=False))
