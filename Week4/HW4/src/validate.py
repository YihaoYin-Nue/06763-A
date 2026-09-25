from __future__ import annotations

import polars as pl

from table import RESULTS, TRAIN, VALIDATION, build_table, load, ridge, rmse, runs, xy

H = 10                                # hyperparameters are chosen at 30 minutes
LAGS = [1, 2, 5, 10, 20, 40]
ALPHAS = [0.01, 1.0, 100.0, 10_000.0]


def sweep(df: pl.DataFrame) -> pl.DataFrame:
    """Fit on runs 1 to 300, score on runs 301 to 400, for every (lag depth, alpha).

    Only runs 1 to 400 are handed to this function: the test runs are not
    loaded here at all, so they cannot play any part in the choice.
    """
    rows = []
    for n_lags in LAGS:
        table, names = build_table(df, H, n_lags)          # lags + valves at t
        X_train, y_train = xy(runs(table, TRAIN), names)
        X_val, y_val = xy(runs(table, VALIDATION), names)
        for alpha in ALPHAS:
            model = ridge(alpha).fit(X_train, y_train)
            rows.append({"h": H, "n_lags": n_lags, "alpha": alpha, "features": len(names),
                         "train_rows": len(y_train),
                         "train_rmse": rmse(y_train - model.predict(X_train)),
                         "val_rmse": rmse(y_val - model.predict(X_val)),
                         "val_persistence_rmse": rmse(y_val - X_val[:, 0])})
    out = pl.DataFrame(rows)
    return out.with_columns(chosen=pl.col("val_rmse") == pl.col("val_rmse").min())


def chosen() -> tuple[int, float]:
    """The setting with the lowest validation RMSE, read back from validation.csv."""
    best = pl.read_csv(RESULTS / "validation.csv").filter(pl.col("chosen")).row(0, named=True)
    return int(best["n_lags"]), float(best["alpha"])


def main() -> pl.DataFrame:
    seen = runs(load(), (TRAIN[0], VALIDATION[1]))        # runs 1 to 400 only
    out = sweep(seen)
    RESULTS.mkdir(exist_ok=True)
    out.write_csv(RESULTS / "validation.csv")
    return out


if __name__ == "__main__":
    out = main()
    print(out.sort("n_lags", "alpha"))
    print("chosen (n_lags, alpha):", chosen())
