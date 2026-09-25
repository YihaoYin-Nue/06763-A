from __future__ import annotations

import numpy as np
import polars as pl

from table import DT_MIN, HORIZONS, RESULTS, SAMPLE, SAMPLES_PER_RUN, T_FIRST, TRAIN, Y, load, runs

MAX_LAG = 60


def autocorrelation(df: pl.DataFrame, max_lag: int = MAX_LAG) -> pl.DataFrame:
    """Autocorrelation of xmeas_18 in the training runs, pairs taken inside each run.

    Samples before T_FIRST are left out: there every run is still leaving the
    same initial steady state, and the forecasts are never scored there.
    For a stationary series persistence has MSE 2 var (1 - rho) and the mean
    has MSE var, so persistence_over_mean = sqrt(2 (1 - rho)) predicts the
    ratio of their RMSEs, and persistence wins only while rho > 0.5.
    """
    # One row per run, so a pair (s, s+k) never spans two runs.
    y = runs(df, TRAIN)[Y].to_numpy().reshape(-1, SAMPLES_PER_RUN)[:, T_FIRST - 1:]
    y = y - y.mean()
    var = np.mean(y ** 2)
    lags = np.arange(1, max_lag + 1)
    rho = np.array([np.mean(y[:, :-k] * y[:, k:]) / var for k in lags])
    return pl.DataFrame({"lag": lags, "minutes": lags * DT_MIN, "acf": rho,
                         "persistence_over_mean": np.sqrt(2 * (1 - rho))})


def main() -> pl.DataFrame:
    acf = autocorrelation(load())
    RESULTS.mkdir(exist_ok=True)
    acf.write_csv(RESULTS / "acf.csv")
    return acf


if __name__ == "__main__":
    acf = main()
    print(acf.filter(pl.col("lag").is_in(HORIZONS)))
    cross = acf.filter(pl.col("acf") < 0.5)["lag"].min()
    print(f"rho first drops below 0.5 at lag {cross} ({cross * DT_MIN} min)")
