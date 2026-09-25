# Report
Yihao Yin (yihaoyin)

Forecasting the Tennessee Eastman stripper temperature `xmeas_18` (deg C) 1 to 40 samples (3 to 120 min) ahead. `uv run python src/pipeline.py` rebuilds every file in `results/`, byte-identical on rerun.

## 1. The table
`build_table(df, h, n_lags)` in `src/table.py` returns one row per (run, t): the lags y[t], y[t-1], ..., y[t-n_lags+1] of `xmeas_18`, the valves `xmv_1` to `xmv_11` at sample t, and the target y[t+h]. All but the target is known at t; the valves are taken at t only, since later moves are not known when the forecast is made. Every lag and the target is `pl.col(Y).shift(k).over(RUN, order_by=SAMPLE)`, so each shift restarts in each run and gives null at its edges instead of reading the neighbouring run. Those rows are dropped, and origins start at t = 40, so every lag depth up to 40 is scored on the same rows. `load()` checks that every run holds samples 1 to 500 exactly once, so a shift by k rows is a shift by k samples. Checked against a per-run array, every lag, valve and target is `xmeas_18` at t-k, t and t+h of the same run.

## 2. Baselines and skill
`results/forecast.csv`, test runs 401 to 500, RMSE in deg C; skill = 1 - direct / min(persistence, mean).

| h | min | persistence | mean | direct | recursive | skill |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 3 | 0.0501 | 0.4231 | 0.0367 | 0.0393 | 0.2680 |
| 5 | 15 | 0.1400 | 0.4244 | 0.0869 | 0.1070 | 0.3791 |
| 10 | 30 | 0.2218 | 0.4260 | 0.1271 | 0.1486 | 0.4268 |
| 20 | 60 | 0.3920 | 0.4289 | 0.2029 | 0.2482 | 0.4825 |
| 40 | 120 | 0.6353 | 0.4345 | 0.3328 | 0.3846 | 0.2340 |

Persistence beats the mean (65.8056 deg C) up to h = 20 (0.3920 against 0.4289) and loses at h = 40 (0.6353 against 0.4345). This matches the autocorrelation of `xmeas_18` in the training runs (`results/acf.csv`): rho is 0.868 at 10 samples and 0.572 at 20, falls below 0.5 at lag 23 (69 min), and is -0.155 at 40. The temperature oscillates, so two hours on it tends to be across the mean and holding today's value points the wrong way. Direct beats the better baseline at all five horizons; skill peaks at one hour, where neither free forecast is good.

## 3. Validation
`make_pipeline(StandardScaler(), Ridge(alpha))` at h = 10 on lags plus valves, fitted on runs 1 to 300 and scored on runs 301 to 400; `src/validate.py` never loads the test runs. Lag depths 1, 2, 5, 10, 20, 40 and alpha 0.01, 1, 100, 10000 give 24 settings (`results/validation.csv`). The lowest validation RMSE, 0.1280 against 0.2279 for persistence, is 40 lags with alpha = 1. Lag depth mattered most: 0.1528 with one lag, 0.1308 with 10, 0.1280 with 20; 40 beat 20 by 0.00005, a tie in practice. A history shows which way the temperature is moving; one value cannot. Alpha from 0.01 to 100 changed only the fourth decimal, since 135,300 training rows hold 51 coefficients in check, and 10000 hurt (0.1364) by shrinking useful ones. Train and validation RMSE agree (0.1277 against 0.1280): nothing to overfit.

## 4. Direct against recursive
Direct is one ridge per horizon on 40 lags plus the valves at t. Recursive is one ridge for y[t+1] on the 40 lags alone, applied h times with each prediction shifted into y[t]. Direct wins at every horizon: 0.0367 against 0.0393 at h = 1, 0.1271 against 0.1486 at h = 10, 0.3328 against 0.3846 at h = 40. At h = 1 nothing compounds, so that gap is the valves alone, mostly the stripper steam valve `xmv_9`, which shows where the temperature is being pushed before it moves. The gap then grows from 0.0026 to 0.0518 deg C: the recursive model was trained for one step and feeds its own errors back, while each direct model is fitted to its own horizon. Recursive still beats the mean at two hours; 40 lags describe the oscillation, and a stable linear recursion relaxes toward the mean rather than diverging. It has no valves because from the second step on it would need the valves at t+1, t+2, ..., which are unknown at t: holding them fixed assumes the controllers stop, and forecasting them needs eleven more models.

## 5. Shuffle against time
Runs 1 to 10 one at a time, h = 10, the direct features, a 100-tree random forest, 5 folds, persistence on the same test folds. Means over the runs of `results/leaky.csv`:

| split | forest | persistence |
|---|---:|---:|
| `KFold(n_splits=5, shuffle=True)` | 0.0960 | 0.2550 |
| `TimeSeriesSplit(n_splits=5, gap=10)` | 0.3448 | 0.2517 |

Shuffled, the forest looks 62% better than persistence; in time order it is 37% worse, and both hold in every one of the ten runs. I would report 0.3448 to a plant manager: in use the model always forecasts a future it has not seen, which is what the time split measures. Shuffled, each test row's neighbours a few minutes either side are in the training set, and their targets y[t+h-1] and y[t+h+1] are almost the answer (rho is 0.994 at one lag), so the forest interpolates between answers it has seen. With about 360 rows of one run it even appears to beat the ridge model trained on 300 runs (0.1271), which only a leak allows. The gap must be at least h because a training row at origin t has target y[t+h]; with a smaller gap the last training targets fall inside the test stretch. In time order the forest loses to persistence because it has only 66 to 366 training rows and cannot predict outside the range of targets it has seen.

## 6. Limits
The model has only seen fault-free runs, so its errors are known only for normal data, and it leans on one input: at h = 10 the steam valve `xmv_9` carries +0.30 deg C per standard deviation (`results/coefficients.csv`), more than three times the next valve (`xmv_5`, -0.09). Suppose the steam supply pressure drops. `xmv_9` still reads the same opening, but less steam passes through it and the stripper cools. Having only ever seen the temperature follow that valve, the model keeps forecasting the temperature this opening has always given. Nothing warns it: every input is in its usual range, the output is one number, and the 0.1271 deg C it scored at 30 minutes describes a plant that behaves like the fault-free simulations, not this one. It needs a separate check, such as an alarm on its own one-step residuals, to say when its training no longer applies.

Generative AI use: I used Claude to help me with the code debug; every number in it is computed by the code in `src/` and saved in `results/`.
