from __future__ import annotations

import numpy as np
import polars as pl
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import KFold, TimeSeriesSplit

from table import RESULTS, RUN, build_table, load, rmse, xy
from validate import chosen

H = 10
RUNS = range(1, 11)


def splits() -> dict:
    return {
        # Rows go to train and test at random, so each test row's neighbours,
        # a few minutes before and after, are usually in the training set.
        "shuffled": KFold(n_splits=5, shuffle=True, random_state=0),
        # Train on the past, skip H origins, test on what follows. A training
        # row at origin t has target y[t+H]; with gap=H the last one still
        # lands before the first test origin, so no training target is taken
        # from the test stretch.
        "time": TimeSeriesSplit(n_splits=5, gap=H),
    }


def score_run(df: pl.DataFrame, run: int, n_lags: int) -> list[dict]:
    """One run on its own: a forest and persistence, scored on the same test folds."""
    table, names = build_table(df.filter(pl.col(RUN) == run), H, n_lags)   # direct features
    X, y = xy(table, names)                    # rows in time order, column 0 is y[t]
    rows = []
    for name, cv in splits().items():
        forest, persistence = [], []
        for train, test in cv.split(X):
            # One thread: parallel trees are summed in a varying order, which
            # changes the last digit of the RMSE from one run to the next.
            model = RandomForestRegressor(n_estimators=100, random_state=0, n_jobs=1)
            model.fit(X[train], y[train])
            forest.append(rmse(y[test] - model.predict(X[test])))
            persistence.append(rmse(y[test] - X[test, 0]))
        rows.append({"run": run, "split": name, "h": H,
                     "model_rmse": float(np.mean(forest)),
                     "persistence_rmse": float(np.mean(persistence))})
    return rows


def main() -> pl.DataFrame:
    df = load()
    n_lags, _ = chosen()
    out = pl.DataFrame([row for run in RUNS for row in score_run(df, run, n_lags)])
    RESULTS.mkdir(exist_ok=True)
    out.write_csv(RESULTS / "leaky.csv")
    return out


if __name__ == "__main__":
    out = main()
    with pl.Config(float_precision=4, tbl_rows=30):
        print(out)
        print(out.group_by("split").agg(pl.col("model_rmse", "persistence_rmse").mean()).sort("split"))
