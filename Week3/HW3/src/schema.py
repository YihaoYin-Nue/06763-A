from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pandera.pandas as pa

from contract import Contract

# Wide enough that a plausible reading passes and an impossible one does not.
UNIT_RANGE = {
    "Mole %": (0.0, 100.0),
    "%": (0.0, 100.0),
    "Deg C": (0.0, 200.0),
    "kPa gauge": (0.0, 4000.0),
    "kscmh": (0.0, 200.0),
    "kg/hr": (0.0, 20000.0),
    "m3/hr": (0.0, 200.0),
    "kW": (0.0, 2000.0),
}

# OPC UA status codes the publisher is documented to send.
STATUSES = {"Good", "Uncertain_SensorNotAccurate", "Bad_DeviceFailure"}

# How far behind the first arrival an event may legitimately be: a replayed
# (historical) message can carry readings a few plant hours old.
LOOKBACK = timedelta(hours=6)
LOOKAHEAD = timedelta(hours=1)

STAMP = pd.DatetimeTZDtype("ms", "UTC")


def build_schema(birth: dict, window: tuple[datetime, datetime]) -> pa.DataFrameSchema:
    tags = birth["tags"]
    names = [t["name"] for t in tags]
    unit = {t["name"]: t["unit"] for t in tags}
    unknown = sorted(set(unit.values()) - set(UNIT_RANGE))
    assert not unknown, f"the birth message declares a unit with no range here: {unknown}"
    low = {name: UNIT_RANGE[u][0] for name, u in unit.items()}
    high = {name: UNIT_RANGE[u][1] for name, u in unit.items()}
    start, end = window

    def value_in_unit_range(frame: pd.DataFrame) -> pd.Series:
        # One range per unit, looked up per row: a whole-frame check.
        # A missing value is not_nullable's failure, not this check's.
        lo = frame["tag"].map(low)
        hi = frame["tag"].map(high)
        return frame["value"].isna() | frame["value"].between(lo, hi)

    def measured_before_published(frame: pd.DataFrame) -> pd.Series:
        return frame["event_time"] <= frame["server_time"]

    # A built-in check reports its `error` text in failure_cases, not its name.
    columns = {
        "tag": pa.Column(str, pa.Check.isin(names, error="tag_declared_in_birth")),
        "value": pa.Column(float, nullable=False),
        "status": pa.Column(str, pa.Check.isin(STATUSES, error="status_known")),
        "event_time": pa.Column(STAMP, pa.Check.in_range(start, end, error="event_time_in_collection_window")),
        "server_time": pa.Column(STAMP),
        "received_at": pa.Column(STAMP),
        "seq": pa.Column(int, pa.Check.in_range(0, 255, error="seq_is_one_byte")),
        "is_historical": pa.Column(bool),
    }
    checks = [
        pa.Check(value_in_unit_range, name="value_in_unit_range"),
        pa.Check(measured_before_published, name="event_time_at_or_before_server_time"),
    ]
    return pa.DataFrameSchema(columns, checks=checks, strict=True, coerce=False)


def collection_window(c: Contract, received: pd.Series) -> tuple[datetime, datetime]:
    first = c.plant_time(received.min().to_pydatetime())
    last = c.plant_time(received.max().to_pydatetime())
    return first - LOOKBACK, last + LOOKAHEAD


def failures(schema: pa.DataFrameSchema, frame: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Validate everything at once (lazy=True) and return, per row index, the
    names of the checks that row failed, plus pandera's failure-case table."""
    try:
        schema.validate(frame, lazy=True)
    except pa.errors.SchemaErrors as err:
        cases = err.failure_cases
        rows = cases.dropna(subset=["index"]).copy()
        rows["index"] = rows["index"].astype(int)
        reasons = (rows.groupby("index")["check"]
                   .agg(lambda s: ";".join(sorted(set(map(str, s))))))
        return reasons, cases
    return pd.Series(dtype=str), pd.DataFrame()
