from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "tep_fault_free_training.parquet"
RESULTS = ROOT / "results"

Y = "xmeas_18"                       # stripper temperature, deg C
RUN = "simulationRun"
SAMPLE = "sample"
XMV = [f"xmv_{i}" for i in range(1, 12)]
DT_MIN = 3                           # minutes between samples

# The fixed choices of the assignment.
HORIZONS = [1, 5, 10, 20, 40]
TRAIN = (1, 300)
VALIDATION = (301, 400)              # hyperparameters only
TEST = (401, 500)                    # touched once, by forecast.py
T_FIRST = 40                         # first forecast origin in every run
SAMPLES_PER_RUN = 500


def load() -> pl.DataFrame:
    """The fault-free training file, sorted, after checking every run is whole.

    A shift by k rows is a shift by k samples only if no run has a missing or
    repeated sample, so that is checked here rather than assumed.
    """
    df = pl.read_parquet(DATA).sort(RUN, SAMPLE)
    per_run = df.group_by(RUN).agg(n=pl.len(), first=pl.col(SAMPLE).min(),
                                   last=pl.col(SAMPLE).max(), unique=pl.col(SAMPLE).n_unique())
    bad = per_run.filter((pl.col("n") != SAMPLES_PER_RUN) | (pl.col("unique") != SAMPLES_PER_RUN)
                         | (pl.col("first") != 1) | (pl.col("last") != SAMPLES_PER_RUN))
    if bad.height or per_run.height != 500:
        raise ValueError(f"expected 500 runs of samples 1..{SAMPLES_PER_RUN}:\n{bad}")
    return df


def runs(frame: pl.DataFrame, which: tuple[int, int]) -> pl.DataFrame:
    return frame.filter(pl.col(RUN).is_between(*which))


def lag_names(n_lags: int) -> list[str]:
    """y[t] first, so column 0 of every feature matrix is the persistence forecast."""
    return ["y[t]"] + [f"y[t-{k}]" for k in range(1, n_lags)]


def build_table(df: pl.DataFrame, h: int, n_lags: int, valves: bool = True
                ) -> tuple[pl.DataFrame, list[str]]:
    """One row per (run, t): the features known at t, and the target y[t+h].

    Lag k is xmeas_18 at sample t-k, for k = 0 .. n_lags-1; the valves are the
    ones at sample t itself, because later valve moves are not known when the
    forecast is made. Every shift runs over(RUN), ordered by sample, so no lag
    and no target reaches into a neighbouring run: at the edges of a run the
    shift gives null and the row is dropped. Origins then start at T_FIRST, so
    every lag depth up to 40 is fitted and scored on the same (run, t) rows.
    """
    if not 1 <= n_lags <= T_FIRST:
        raise ValueError(f"lag depth must be 1..{T_FIRST}, got {n_lags}")
    names = lag_names(n_lags)
    features = {name: pl.col(Y).shift(k).over(RUN, order_by=SAMPLE)
                for k, name in enumerate(names)}
    if valves:
        features |= {v: pl.col(v) for v in XMV}
    table = (df.select(RUN, pl.col(SAMPLE).alias("t"), **features,
                       target=pl.col(Y).shift(-h).over(RUN, order_by=SAMPLE))
             .drop_nulls()
             .filter(pl.col("t") >= T_FIRST)
             .sort(RUN, "t"))
    return table, list(features)


def xy(table: pl.DataFrame, names: list[str]) -> tuple[np.ndarray, np.ndarray]:
    return table.select(names).to_numpy(), table["target"].to_numpy()


def train_mean(df: pl.DataFrame) -> float:
    """The mean forecast: xmeas_18 over all samples of the training runs."""
    return float(runs(df, TRAIN)[Y].mean())


def ridge(alpha: float):
    # The scaler sits inside the pipeline, so it is fitted on training rows only.
    return make_pipeline(StandardScaler(), Ridge(alpha=alpha))


def rmse(error) -> float:
    return float(np.sqrt(np.mean(np.square(np.asarray(error)))))


if __name__ == "__main__":
    df = load()
    for h in HORIZONS:
        table, names = build_table(runs(df, TEST), h, n_lags=10)
        print(f"h={h:>2}: {table.height:,} test rows, origins {table['t'].min()}..{table['t'].max()}")
    print(table.select(RUN, "t", *names[:3], "xmv_1", "target").head(3))
