# GTA VI Launch-Day Twitch Forecaster

An entry for make.fun's **gta6-twitch-ccu-forecast-01** bounty: forecast,
hour by hour, how many people watch Grand Theft Auto VI live on Twitch
over the 24 UTC hours of its release day, 2026-11-19 — from everything
recorded up to the end of the day's first hour, that hour's count
included.

## What it does

`main.py` (Python 3, standard library only) is run once per case in an
offline sandbox. It reads the case file in the input directory and writes
five quantiles (5%, 25%, 50%, 75%, 95%) of the Twitch viewer count for
each of hours 2 to 24 of the day.

## How it works

A launch day's shape is read off the past launches the case itself
carries (its `comparables`). For every comparable with a usable release
day, the day's hourly viewers are divided by its own hour-1 viewers,
giving a ratio curve in log space. The target's ratios are a weighted
blend of those curves, where a comparable counts more when it resembles
the target on:

- **scale** — its hour-1 viewers, in log units;
- **pre-day surge** — the highest of the twelve hours before the day,
  over hour 1 (a rolling midnight unlock has already surged somewhere
  in the world before the UTC day starts; a single global unlock has
  not);
- **unlock kind** — rolling local-midnight unlock versus one worldwide
  moment, read from the case's own unlock notes.

The five quantiles of an hour are the weighted quantiles of the blended
log-ratio distribution for that hour, lightly smoothed across
neighbouring hours, so the spread is the honest spread of similar past
launches. The program is deterministic: sorted iteration, no
randomness, no clock, no network — the same bytes come out of every
run.

Validated by leave-one-out over the 37 comparable launches with a full
release day: mean pinball loss (log scale) ≈ 0.43, against ≈ 0.93 for
the flat-at-hour-1 house baseline on the same launches.

## Running it

```
mkdir -p out
MAKEFUN_INPUT_DIR=<dir with .in case files> MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Without the environment variables it reads `/input` and writes
`/output`, as the grader's sandbox expects.

## License

MIT — see `LICENSE`.
