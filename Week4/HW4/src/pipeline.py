from __future__ import annotations

import acf
import forecast
import leaky
import validate


def run() -> None:
    a = acf.main()
    cross = a.filter(a["acf"] < 0.5)["lag"].min()
    print(f"acf          rho first below 0.5 at lag {cross}")

    v = validate.main()
    n_lags, alpha = validate.chosen()
    print(f"validate     {v.height} settings on runs 301 to 400; chosen {n_lags} lags, alpha {alpha}")

    f = forecast.main()
    wins = int((f["skill"] > 0).sum())
    print(f"forecast     direct beats the better baseline at {wins} of {f.height} horizons")

    k = leaky.main().group_by("split").agg(leaky.pl.col("model_rmse").mean()).sort("split")
    print("leaky        mean forest RMSE " + ", ".join(f"{s} {m:.4f}" for s, m in k.iter_rows()))


if __name__ == "__main__":
    run()
