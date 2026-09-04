import json, time
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
cols = ["date","time","epoch","moteid","temperature","humidity","light","voltage"]
t0=time.time()
raw = pd.read_csv(HERE/".cache/data.txt", sep=r"\s+", names=cols, header=None,
                  engine="c", on_bad_lines="skip")
print(f"{len(raw):,} raw rows ({time.time()-t0:.1f}s)", flush=True)

df = raw.dropna(subset=["moteid"]).copy()
df["moteid"] = df["moteid"].astype(int)
df = df[df.moteid.between(1,54)]
df["ts"] = pd.to_datetime(df["date"]+" "+df["time"], format="mixed", errors="coerce")
df = df.dropna(subset=["ts"])
long = (df.melt(id_vars=["moteid","ts"],
                value_vars=["temperature","humidity","light","voltage"],
                var_name="variable", value_name="value")
          .dropna(subset=["value"])
          .rename(columns={"moteid":"sensor_id"})
          .drop_duplicates(["sensor_id","ts","variable"]))
print(f"{len(long):,} tidy readings, {long.sensor_id.nunique()} sensors", flush=True)

t0=time.time()
long[["sensor_id","ts","variable","value"]].to_csv(HERE/"tidy.csv", index=False, header=False)
print(f"tidy.csv written ({time.time()-t0:.1f}s)", flush=True)

json.dump({"raw_rows": int(len(raw)),
           "tidy_rows": int(len(long)),
           "n_sensors": int(long.sensor_id.nunique()),
           "sensor_ids": sorted(int(s) for s in long.sensor_id.unique())},
          open(HERE/"prep_stats.json","w"), indent=2)
print("DONE", flush=True)
