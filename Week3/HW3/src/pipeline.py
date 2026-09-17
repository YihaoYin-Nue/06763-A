from __future__ import annotations

import clean
import disturbance
import gate_test
import grid
import stream
import windows
from contract import contract
from decode import decode


def run() -> None:
    c = contract()

    readings = decode(show_plan=True).to_pandas()
    print(f"decode       {len(readings):,} readings from {readings['line'].nunique()} messages")

    tidy, quarantine, counts = clean.run(c, readings)
    print(f"clean        {counts['duplicates_removed']:,} duplicates removed, "
          f"{len(quarantine)} quarantined, {len(tidy):,} tidy rows")

    s = stream.run(c, readings, tidy)["summary"]
    print(f"stream       {s['late_pct']}% late; watermark sweep and clock comparison written")

    g = grid.run(c, readings, tidy, quarantine)["summary"]
    holes = g["empty_before_fill"]
    print(f"grid         {g['rows']} rows; empty before fill: continuous {holes['continuous']['empty']}, "
          f"analyser {holes['analyser']['empty']}")

    w = windows.main()
    print(f"windows      {len(w)} windows, {int((~w['complete']).sum())} incomplete")

    d = disturbance.main()["summary"]
    print(f"disturbance  {len(d['episodes'])} episodes, {len(d['ambiguous'])} ambiguous")

    gate_test.main()
    print("gate test    passed")


if __name__ == "__main__":
    run()
