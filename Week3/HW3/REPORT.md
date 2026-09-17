# Report
Yihao Yin (yihaoyin)

## Part 1 The Three Clocks
### Results
| messages | tags | timestamp minus sourceTimestamp (plant s) |
|---|---|---:|
| live (445) | 34 continuous | 0 |
| live | 14 analysers, every 0.1 h | 0 or 180 |
| live | 5 analysers, every 0.25 h | 0 to 720 |
| replayed, isHistorical (129) | all 53 | 1620 to 7200, median 3960 |
### Discussion
Windows are keyed on sourceTimestamp, the event time, because it is the only clock that says when the plant was in the state a value describes; the message timestamp says when a batch was assembled and receivedAt when this laptop saw it. 402 of the 443 message timestamps carry more than one event time.
The largest gap, 7200 plant s, is on a replayed message: the link dropped four times, for 32 to 46 wall seconds (77 to 111 plant minutes), and each backlog was resent stamped with the time of resending. On live messages the gap comes from the analysers, which republish an analysis unchanged until the next one, so the lag reaches analyserIntervalHours x 3600 - 180, 720 s for the 0.25 h analysers.
receivedAt is on another machine: converted to plant time it lands a median 31 plant s before the live timestamp, so this laptop runs about 0.2 wall s behind the publisher.

## Part 2 What You Collected
### Results
| item | count |
|---|---:|
| messages | 575 (574 telemetry, 1 birth), 28.95 plant hours |
| readings | 30422 |
| Good / Uncertain / Bad | 30337 / 70 / 15 |
| duplicates removed | 6333 |
| rejected by the schema | 3 |
| quarantined rows | 88 (70 Uncertain, 15 Bad, 3 value_in_unit_range) |
| tidy rows | 24001 |
| late readings | 5356 (22.32%) |
### Discussion
The pipeline quarantines, then deduplicates, then validates. A status belongs to a delivery, not a measurement: 28 analyser values arrived Good in one message and Uncertain or Bad in another, with the same value. So each non-Good delivery is quarantined on its own, and the Good rows are deduplicated on (tag, event_time), keeping the first arrival; seq wraps every 256 messages and is not an identity. The 6333 duplicates are 6266 analyser republishes and 67 rows of two messages delivered twice.
The pandera schema is generated from the birth tags (names, and a range per unit), plus a known status set and an event_time inside the collection window. With lazy=True it rejects 3 of the 24004 Good measurements, XMEAS_37 product D at -0.0032 to -0.0017 mole %: analyser noise near zero, but not physical. The Uncertain readings all look plausible, so the status decides, not the value.
The gate test breaks 5 of 300 copied rows (undeclared tag, out-of-range value, missing value, unknown status, wrong year); each is caught by its own check and no clean row is flagged. Two runs of src/pipeline.py give 14 byte-identical outputs, so the pipeline is idempotent.

## Part 3 What You Filled
### Results
| tags | cells | empty before filling | cause | fill |
|---|---:|---:|---|---|
| 34 continuous | 19584 | 192 (0.98%) | 136 in 4 samples never delivered, 56 quarantined | linear interpolation in time |
| 19 analysers | 10944 | 6340 (57.93%) | 6337 between analyses, 3 quarantined | last analysis held forward |
### Discussion
The grid has 576 rows every 180 plant s, from 02:49:54 on 2029-07-20 to 07:34:54 the next day. It starts at the first continuous sample, not at the earliest event time, a lagged analyser timestamp that would open the grid with rows no continuous tag could fill. The spine is built first and the pivot joined onto it.
Each continuous hole, from the four samples never delivered (04:49:54, 11:04:54, 21:22:54, 21:46:54) or the 56 quarantined readings, is one sample between two measured ones, so it is interpolated in time. Quarantine runs first, so no Bad or Uncertain value is ever used.
The analyser holes are by design: 14 analysers report every second row and 5 every fifth, and (14 x 1/2 + 5 x 4/5) / 19 = 57.9%. An analysis is the latest composition known until the next one, so it is held forward, as the plant holds it; interpolation would use an analysis that had not happened yet. The 5 holes in the first row take the 02:37:54 analysis from just before the grid, so nothing comes from the future. Boolean `<tag>_filled` columns mark all 6532 filled cells.

## Part 4 The Watermark
### Results
| allowance (plant min) | wall s | complete % | dropped as late | still waiting |
|---:|---:|---:|---:|---:|
| 0 | 0 | 77.68 | 5356 | 0 |
| 15 | 6.25 | 77.68 | 5356 | 202 |
| 30 | 12.5 | 78.16 | 5241 | 419 |
| 60 | 25 | 87.61 | 2974 | 837 |
| 90 | 37.5 | 96.65 | 805 | 1256 |
| 120 | 50 | 100.00 | 0 | 1676 |
| 240 | 100 | 100.00 | 0 | 3353 |
### Discussion
A reading is late by how far its event_time is behind the newest event_time of any message that arrived before it, in line order, since one replay burst can share a receivedAt millisecond. All 5356 late readings came from the four replays (median 66, max 108 plant minutes); no live reading was late.
I would set the allowance to 120 plant minutes, 50 wall seconds. It is the smallest that admits everything here, with 12 minutes to spare, and close to the p95 lateness (117) the assignment reports for 6000 messages, so in a longer run under 1% of readings would miss; re-emitting their windows is cheaper than holding every window for the 198-minute worst case. Too short loses whole outages, not stragglers: at 30 minutes 22% miss, and the windows over each outage close nearly empty yet final. Too long delays every answer: at 240 minutes a disturbance is reported four plant hours late, with 3353 readings still waiting at the end, twice as many as at 120.

## Part 5 Event Time vs. Processing Time
### Results
Readings per window and the mean reactor pressure (XMEAS_7, kPa), over 30-minute windows on event_time and on receivedAt converted to plant time. Replayed readings in brackets.

| window, 2029-07-20 | readings, event_time | readings, receivedAt | mean, event_time | mean, receivedAt |
|---|---:|---:|---:|---:|
| 08:30 | 419 | 0 | 2700.70 | - |
| 09:30 | 419 | 338 (251) | 2714.11 | 2699.96 |
| 10:00 | 419 | 1541 (1122) | 2711.06 | 2709.70 |
| 10:30 | 419 | 419 | 2701.20 | 2701.20 |
| 20:30 | 418 | 1676 (1258) | 2709.18 | 2706.52 |
### Discussion
Outside the outages the two clocks give identical windows. During an outage nothing is received, so 8 of 59 windows are empty on receivedAt, and the backlog then lands in the next windows, worst at 20:30 with 1676 readings against 418. The mean differs most at 09:30, by 14.14 kPa or 1.7 standard deviations: on receivedAt that window mixes the live 09:55 and 09:58 samples with replayed ones from 08:16 to 08:31, so it shows roughly the 08:30 pressure. A processing-time dashboard would show a gap and then a burst of the past, both at the wrong time.

## Part 6 The Disturbance
### Disturbance Results
| episode | from | recovered by | peak score | tags, largest z |
|---|---|---|---:|---|
| A | 2029-07-20 03:00 or earlier | 04:30 | 1.73 | XMV_10 reactor cooling water flow -22.3, XMEAS_9 reactor temperature +9.8, XMV_4 A and C feed -3.2 |
| B | 2029-07-21 04:00 | 05:30 | 1.66 | XMV_5 compressor recycle valve -8.6, XMEAS_20 compressor work -6.5, XMEAS_13 separator pressure -4.5, XMEAS_18 stripper temperature -3.3 |
| unclear | 2029-07-20 08:30 | 10:30 | 1.24 | XMV_10 and XMEAS_9, swinging between -5.9 and +7.9 |
### Disturbance Discussion
A window's score is the mean absolute z of its means over 33 continuous tags (XMV_12, agitator speed, never changes). The baseline uses quiet windows only: a first pass with the median and MAD, which disturbed windows cannot inflate, marks the 40 of 57 complete windows with no tag beyond 3 as quiet, and their mean and standard deviation give the final z. Windows above 0.81 + 3 x 0.17 = 1.32 stand out, the two incomplete windows (4 and 2 samples) are left out, and an episode extends over neighbours where a tag is still beyond 3.
In A the cooling water flow falls first, at 03:00, with the reactor temperature still normal; by 03:30 the temperature is up and the flow has swung back (+10.4), which looks like the temperature loop reacting. A is in the first complete window, so it may have begun before collection. B moves the compressor and separator, with XMV_5 already at -4.7 at 04:00. I am confident these tags moved then; which fault moved them is beyond 30-minute means.
08:30 to 10:30 is genuinely ambiguous: two reactor tags swing by 3 to 8, but averaged with 31 quiet tags and partly cancelled within each window, the score stays under the cut. It may be a second cooling disturbance or A still ringing. Its values are measured (20.9% filled, like quiet windows), but analysers are not scored and 40 quiet windows is a thin baseline, so smaller disturbances would be missed.

## What .explain() Showed
The plan pushes the topic filter and the row index into the NDJSON scan, but still reads all three top-level fields (PROJECT 3/3 COLUMNS) and extracts struct fields above the scan, so every line's whole payload is parsed, birth fields included. The casts to Float64, Int64 and Boolean disappear, because the inferred schema already had those types.

## AI Use
Generative AI (Claude Code) was used to help write the pipeline code and polishing this report; every number here was produced by the scripts in this repository on my own machine.
