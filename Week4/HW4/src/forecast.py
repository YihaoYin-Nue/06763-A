from __future__ import annotations

import numpy as np
import polars as pl

from table import (DT_MIN, HORIZONS, RESULTS, RUN, TEST, TRAIN, build_table, load, ridge,
                   runs, train_mean, xy)
from validate import chosen

KEY = ["run", "t", "h"]
METHODS = ["persistence", "mean", "direct", "recursive"]


def origins(test: pl.DataFrame, h: int) -> pl.DataFrame:
    return test.select(pl.col(RUN).alias("run"), "t", pl.lit(h, dtype=pl.Int64).alias("h"))


def baselines(df: pl.DataFrame, mean: float) -> pl.DataFrame:
    """Persistence and the mean on every test origin, one row per (run, t, h)."""
    parts = []
    for h in HORIZONS:
        table, _ = build_table(runs(df, TEST), h, n_lags=1, valves=False)
        parts.append(origins(table, h).with_columns(
            y_true=table["target"],
            persistence=table["y[t]"],          # y[t+h] = y[t]
            mean=pl.lit(mean),                  # the training mean, whatever t is
        ))
    return pl.concat(parts)


def direct(df: pl.DataFrame, n_lags: int, alpha: float) -> tuple[pl.DataFrame, pl.DataFrame]:
    """One ridge model per horizon, lags of xmeas_18 plus the valves at t -> y[t+h].

    Also returns each model's coefficients on the standardized features, in
    deg C per standard deviation of the input, to show what it leans on.
    """
    parts, coefs = [], []
    for h in HORIZONS:
        table, names = build_table(df, h, n_lags, valves=True)
        model = ridge(alpha).fit(*xy(runs(table, TRAIN), names))
        test = runs(table, TEST)
        parts.append(origins(test, h).with_columns(
            direct=pl.Series(model.predict(test.select(names).to_numpy()))))
        coefs.append(pl.DataFrame({"h": [h] * len(names), "feature": names,
                                   "coef": model[-1].coef_}))
    return pl.concat(parts), pl.concat(coefs)


def recursive(df: pl.DataFrame, n_lags: int, alpha: float) -> pl.DataFrame:
    """One model for y[t+1] from lags of xmeas_18 only, walked forward h steps.

    Each step's prediction becomes the new y[t] and the oldest lag drops off.
    The valves are left out because step two onwards would need the valves at
    t+1, t+2, ..., which are not known at t. The path from an origin does not
    depend on h, so it is walked once to the longest horizon, and read off at
    each horizon on the way.
    """
    table, names = build_table(df, 1, n_lags, valves=False)
    one_step = ridge(alpha).fit(*xy(runs(table, TRAIN), names))
    test = runs(table, TEST)                    # origins 40..499, every horizon's origins
    lags = test.select(names).to_numpy()
    parts = []
    for step in range(1, max(HORIZONS) + 1):
        nxt = one_step.predict(lags)
        lags = np.column_stack([nxt, lags[:, :-1]])
        if step in HORIZONS:
            parts.append(origins(test, step).with_columns(recursive=pl.Series(nxt)))
    return pl.concat(parts)


def score(pred: pl.DataFrame, methods: list[str]) -> pl.DataFrame:
    """RMSE of each method at each horizon, over the same test origins."""
    return (pred.group_by("h")
            .agg(pl.len().alias("rows"),
                 *[((pl.col(m) - pl.col("y_true")) ** 2).mean().sqrt().alias(f"{m}_rmse")
                   for m in methods])
            .sort("h"))


def main() -> pl.DataFrame:
    df = load()
    n_lags, alpha = chosen()                    # picked on runs 301 to 400 by validate.py
    direct_pred, coefs = direct(df, n_lags, alpha)
    # The baselines fix the rows: every test origin t = 40 .. 500-h, for each h.
    pred = (baselines(df, train_mean(df))
            .join(direct_pred, on=KEY, how="left")
            .join(recursive(df, n_lags, alpha), on=KEY, how="left")
            .select(*KEY, "y_true", *METHODS)
            .sort("h", "run", "t"))
    if pred.null_count().sum_horizontal().item():
        raise ValueError("a test origin is missing a forecast")

    better = pl.min_horizontal("persistence_rmse", "mean_rmse")
    table = (score(pred, METHODS)
             .with_columns(minutes=pl.col("h") * DT_MIN,
                           skill=1 - pl.col("direct_rmse") / better,
                           recursive_skill=1 - pl.col("recursive_rmse") / better)
             .select("h", "minutes", "rows", *[f"{m}_rmse" for m in METHODS],
                     "skill", "recursive_skill"))
    RESULTS.mkdir(exist_ok=True)
    pred.write_parquet(RESULTS / "predictions.parquet")
    table.write_csv(RESULTS / "forecast.csv")
    coefs.write_csv(RESULTS / "coefficients.csv")
    return table


if __name__ == "__main__":
    print("chosen (n_lags, alpha):", chosen())
    with pl.Config(float_precision=4):
        print(main())
