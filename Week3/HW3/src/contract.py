from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_GLOB = "raw/stream-*.ndjson"
PROCESSED = ROOT / "processed"
RESULTS = ROOT / "results"

TELEMETRY = "plant/tep/telemetry"
BIRTH = "plant/tep/birth"

SAMPLE = timedelta(seconds=180)
WINDOW = "30m"


def raw_files() -> list[Path]:
    files = sorted(ROOT.glob(RAW_GLOB))
    if not files:
        raise SystemExit(f"no {RAW_GLOB} under {ROOT}; run the collector first")
    return files


def load_birth() -> dict:
    """The first birth message in the capture. The broker retains it, so the
    collector always writes it as the first line of a run."""
    for path in raw_files():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if f'"topic":"{BIRTH}"' in line[:80]:
                    return json.loads(line)["payload"]
    raise SystemExit("no birth message in the capture")


class Contract:
    def __init__(self, birth: dict):
        self.birth = birth
        self.tags: list[dict] = birth["tags"]
        self.names: list[str] = [t["name"] for t in self.tags]
        self.unit: dict[str, str] = {t["name"]: t["unit"] for t in self.tags}
        self.analysers: list[str] = [t["name"] for t in self.tags
                                     if t.get("analyserIntervalHours") is not None]
        self.continuous: list[str] = [n for n in self.names if n not in self.analysers]
        self.acceleration: float = float(birth["accelerationFactor"])
        self.plant_epoch: datetime = datetime.fromisoformat(birth["plantEpoch"].replace("Z", "+00:00"))
        self.sample_seconds: int = int(birth["sampleIntervalSeconds"])

    def plant_time(self, wall):
        """receivedAt_plant = plantEpoch + (receivedAt - plantEpoch) * accelerationFactor

        Works on one datetime or on a pandas Series of them."""
        return self.plant_epoch + (wall - self.plant_epoch) * self.acceleration


def contract() -> Contract:
    return Contract(load_birth())
