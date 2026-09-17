from __future__ import annotations

import numpy as np
import pandas as pd

from contract import PROCESSED, RESULTS, contract
from decode import TIDY_COLUMNS
from schema import UNIT_RANGE, build_schema, collection_window, failures

COPIED = 300


def break_rows(frame: pd.DataFrame, unit: dict[str, str]) -> dict[int, str]:
    """Break one row per failure the assignment names. Returns {row: what was done}."""
    broken = {}

    row = 10
    frame.loc[row, "tag"] = "XMEAS_99_not_in_birth"
    broken[row] = "undeclared tag"

    row = 60
    high = UNIT_RANGE[unit[frame.loc[row, "tag"]]][1]
    frame.loc[row, "value"] = high * 10
    broken[row] = f"value 10x the top of its unit range ({high * 10:g})"

    row = 110
    frame.loc[row, "value"] = np.nan
    broken[row] = "missing value on a Good reading"

    row = 160
    frame.loc[row, "status"] = "Questionable"
    broken[row] = "unknown status code"

    row = 210
    frame.loc[row, "event_time"] = frame.loc[row, "event_time"] - pd.DateOffset(years=1)
    broken[row] = "event_time one year early"

    return broken


def main() -> str:
    c = contract()
    tidy = pd.read_parquet(PROCESSED / "tidy.parquet")
    schema = build_schema(c.birth, collection_window(c, tidy["received_at"]))

    copy = tidy.sample(n=COPIED, random_state=6763).reset_index(drop=True)[TIDY_COLUMNS]
    out = [f"gate test: {COPIED} clean rows copied from processed/tidy.parquet"]

    reasons, _ = failures(schema, copy)
    out.append(f"  before breaking anything: {len(reasons)} rows fail the schema")

    before = copy.copy()
    broken = break_rows(copy, c.unit)
    out.append(f"  broke {len(broken)} rows on purpose, then validated with lazy=True\n")

    reasons, cases = failures(schema, copy)
    unrowed = cases[cases["index"].isna()] if len(cases) else cases
    if len(unrowed):
        out.append("failures not tied to a row:\n" + unrowed.to_string() + "\n")

    out.append("which check caught which rows (from SchemaErrors.failure_cases):")
    by_check = cases.dropna(subset=["index"]).groupby("check")["index"].agg(
        lambda s: sorted(set(s.astype(int))))
    for check, rows in by_check.items():
        out.append(f"  {check:<34} rows {rows}")

    out.append("\nrow by row:")
    for row, what in broken.items():
        was, now = before.loc[row], copy.loc[row]
        changed = [col for col in TIDY_COLUMNS if not (pd.isna(was[col]) and pd.isna(now[col])) and was[col] != now[col]]
        detail = ", ".join(f"{col}: {was[col]} -> {now[col]}" for col in changed)
        out.append(f"  row {row:>3}  {what}\n            {detail}\n"
                   f"            caught by: {reasons.get(row, 'NOTHING')}")

    missed = [row for row in broken if row not in reasons.index]
    false_alarms = [row for row in reasons.index if row not in broken]
    out.append(f"\nbroken rows caught: {len(broken) - len(missed)} of {len(broken)}"
               + (f"  (missed {missed})" if missed else ""))
    out.append(f"clean rows flagged: {len(false_alarms)}" + (f"  {false_alarms}" if false_alarms else ""))
    report = "\n".join(out)

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "gate_test.txt").write_text(report + "\n", encoding="utf-8")
    if missed or false_alarms:
        raise SystemExit(report + "\n\nGATE TEST FAILED")
    return report


if __name__ == "__main__":
    print(main())
