# SensorLab: reproducible air quality calibration

## 1. Project purpose

A sensor analysis can produce a reasonable result and still be difficult to reproduce. The result may change when the package versions, data path, or train/test split changes. This project rebuilds a simple air-quality calibration analysis as a Python package and controls those sources of variation. `uv` records the environment, all paths are relative to the project, the split takes an explicit seed, and MLflow keeps the run history. Consequently, a reported result can be traced to the code, data, and seed that produced it.

The analysis uses the `PT08.S1(CO)` sensor response to predict the reference carbon-monoxide measurement `CO(GT)` with a linear regression model. The raw data come from the [UCI Air Quality Data Set](https://archive.ics.uci.edu/dataset/360/air-quality).

## 2. Rebuild the environment

Install `uv` and open a terminal in the project root. Then run:

```bash
uv sync
```

The project pins its Python version in `.python-version` and records the exact package resolution in `uv.lock`. Deleting `.venv` and running `uv sync` again rebuilds the environment from these files without relying on packages already installed on the machine.

## 3. Get the data

The project downloads the data when it is needed. If the requested CSV is absent, `sensorlab.dataprocess.load()` downloads the UCI archive and writes the raw file to:

```text
data/AirQualityUCI.csv
```

`load()` reads the semicolon-separated file and its comma decimal marks. `clean()` removes empty rows and columns, constructs the timestamp, and replaces the `-200` missing-value sentinel with `NaN`. The raw data remain under `data/` and are excluded from Git.

## 4. Reproduce the result

From the project root, run:

```bash
uv run python -m sensorlab.train --seed 0
```

The seed `0` run should print:

```text
r2=0.7755448891992244
```

Repeating the command with seed `0` produces the same value. Changing the seed changes the train/test split and should produce a different value, for example:

```bash
uv run python -m sensorlab.train --seed 1
```

MLflow writes the seed, data filename, and `r2` metric for each run to the local `mlflow.db` store. To inspect the recorded runs, use:

```bash
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db
```

## 5. Generative-AI use

I used OpenAI Codex during this assignment to diagnose problems in the environment and code. It also identified that an active VS Code Jupyter kernel was blocking the evidence script from deleting and rebuilding `.venv`. I ran the project locally to check the workflow and am responsible for understanding the final code and results.
