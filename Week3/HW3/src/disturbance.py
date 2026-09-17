from __future__ import annotations

import json

import numpy as np
import pandas as pd

from contract import RESULTS, Contract, contract

WINDOW = pd.Timedelta(minutes=30)
QUIET_Z = 3.0        # a window is quiet when no tag is beyond this many sigmas
CUT_SD = 3.0         # a window stands out when its score exceeds quiet mean + CUT_SD * quiet sd
SINGLE_TAG_Z = 4.0   # outside an episode, one tag this far out is reported as ambiguous


def score_windows(windows: pd.DataFrame, c: Contract) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Score each complete window against a baseline built from quiet windows only.
    Returns (per-window table, z-scores, method facts)."""
    full = windows[windows["complete"]].set_index("window_start")[c.continuous]
    flat = [t for t in full.columns if full[t].std() == 0]
    full = full.drop(columns=flat)

    # Pass 1: median and MAD over every complete window. Both ignore a few
    # extreme windows, so the disturbance cannot hide itself in this yardstick.
    med = full.median()
    mad = ((full - med).abs().median() * 1.4826).replace(0, np.nan)
    robust = (full - med) / mad
    quiet = ~(robust.abs() > QUIET_Z).any(axis=1)

    # Pass 2: mean and standard deviation from the quiet windows only.
    sd = full[quiet].std().replace(0, np.nan)
    z = ((full - full[quiet].mean()) / sd).dropna(axis=1, how="all")
    score = z.abs().mean(axis=1)
    cut = float(score[quiet].mean() + CUT_SD * score[quiet].std())

    table = pd.DataFrame({
        "score": score.round(4),
        "quiet": quiet,
        "standout": score > cut,
        "max_abs_z": z.abs().max(axis=1).round(3),
        "top_tags": z.abs().apply(lambda r: "; ".join(f"{t} {z.loc[r.name, t]:+.1f}"
                                                      for t in r.nlargest(3).index), axis=1),
    })
    facts = {
        "complete_windows": len(full),
        "incomplete_windows_excluded": int((~windows["complete"]).sum()),
        "tags_scored": z.shape[1],
        "tags_excluded_no_spread": flat + sorted(set(full.columns) - set(z.columns)),
        "quiet_windows": int(quiet.sum()),
        "quiet_score_mean": round(float(score[quiet].mean()), 4),
        "quiet_score_sd": round(float(score[quiet].std()), 4),
        "cut": round(cut, 4),
    }
    return table, z, facts


def runs(flags: pd.Series) -> list[pd.Index]:
    """Consecutive True stretches, as lists of window starts."""
    ids = (flags != flags.shift()).cumsum()
    return [group.index for _, group in flags[flags].groupby(ids[flags])]


def episodes(table: pd.DataFrame, z: pd.DataFrame, c: Contract) -> tuple[list[dict], list[dict]]:
    desc = {t["name"]: t["description"] for t in c.tags}
    moving = table["max_abs_z"] > QUIET_Z
    starts = list(table.index)
    found, covered = [], set()
    for core in runs(table["standout"]):
        # Widen the core over neighbouring windows where some tag is still
        # beyond QUIET_Z: that is the onset before and the recovery after.
        lo, hi = starts.index(core[0]), starts.index(core[-1])
        while lo > 0 and moving.iloc[lo - 1]:
            lo -= 1
        while hi < len(starts) - 1 and moving.iloc[hi + 1]:
            hi += 1
        span = starts[lo:hi + 1]
        covered.update(span)
        peak = table.loc[core, "score"].idxmax()
        biggest = z.loc[span].abs().max().nlargest(5)
        found.append({
            "disturbed_from": str(span[0]),
            "recovered_by": str(span[-1] + WINDOW),
            "standout_windows": [str(t) for t in core],
            "onset_or_recovery_windows": [str(t) for t in span if t not in core],
            "starts_at_first_complete_window": lo == 0,
            "peak_window": str(peak),
            "peak_score": float(table.loc[peak, "score"]),
            "tags": [{"tag": t, "description": desc[t], "max_abs_z": round(float(v), 1),
                      "z_at_peak": round(float(z.loc[peak, t]), 1)} for t, v in biggest.items()],
        })

    ambiguous = []
    outside = (table["max_abs_z"] > SINGLE_TAG_Z) & ~table.index.isin(list(covered))
    for span in runs(outside):
        tags = z.loc[span].abs().max()
        tags = tags[tags > SINGLE_TAG_Z].sort_values(ascending=False)
        ambiguous.append({
            "from": str(span[0]),
            "to": str(span[-1] + WINDOW),
            "scores": [float(table.loc[t, "score"]) for t in span],
            "tags": [{"tag": t, "description": desc[t],
                      "z_by_window": [round(float(z.loc[w, t]), 1) for w in span]} for t in tags.index],
        })
    return found, ambiguous


def main() -> dict:
    c = contract()
    windows = pd.read_csv(RESULTS / "windows.csv", parse_dates=["window_start"])
    table, z, facts = score_windows(windows, c)
    found, ambiguous = episodes(table, z, c)
    summary = {"method": {"quiet_z": QUIET_Z, "cut_sd": CUT_SD, "single_tag_z": SINGLE_TAG_Z, **facts},
               "episodes": found, "ambiguous": ambiguous}

    RESULTS.mkdir(exist_ok=True)
    table.join(z.round(3).add_prefix("z_")).reset_index().to_csv(RESULTS / "disturbance.csv", index=False)
    (RESULTS / "disturbance.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return {"table": table, "z": z, "summary": summary}


if __name__ == "__main__":
    out = main()
    print(json.dumps(out["summary"], indent=2))
