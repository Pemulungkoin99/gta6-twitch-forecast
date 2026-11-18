"""Hour-by-hour Twitch viewer forecaster for a big game launch day.

Written for make.fun's gta6-twitch-ccu-forecast-01 (program/v1). The program
is run once per case in an offline sandbox: it reads the case file in the
input directory (everything the house recorded up to the end of release
day's first hour, that hour's viewer count included, plus a library of past
launches' hourly curves) and writes five quantiles (5/25/50/75/95%) of the
viewer count for each of hours 2..24 of the day.

How the forecast works
----------------------
A launch day's shape is read off the past launches the case itself carries
(the `comparables`). For every comparable with a usable release day, the
day's hourly viewers are divided by its own hour-1 viewers, giving a ratio
curve in log space. The target's ratios are a weighted blend of those
curves, where a comparable counts more when it resembles the target on
three things the case shows before the checkpoint:

  * scale: its hour-1 viewers, in log units (a launch that opened to
    300,000 viewers moves like one, not like a 400-viewer midnight);
  * pre-day surge: the highest of the twelve hours before the day, over
    hour 1 (a rolling midnight unlock has already surged somewhere in the
    world before the UTC day starts; a single global unlock has not);
  * unlock kind: rolling local-midnight unlock versus one worldwide
    moment, read from the case's own unlock notes.

The five quantiles of an hour are the weighted quantiles of the blended
log-ratio distribution for that hour, so the spread is the honest spread
of similar past launches, hour by hour. Everything is deterministic:
sorted iteration, no randomness, no clock, no network, standard library
only, and the same bytes come out of every run.
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The grader runs python3 -P, which leaves this file's directory off
# sys.path; put it back first so a sibling module would import cleanly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
HOURS = list(range(2, 25))  # the hours a case asks us to forecast

# Kernel bandwidths (log units) for the numeric similarity features,
# and the weight multipliers for the unlock-kind match.
SCALE_BW = 1.15
SURGE_BW = 0.85
CLASS_SAME = 3.0
CLASS_DIFF = 0.4
WEIGHT_FLOOR = 0.003
# Calibration knob: the quantile tracks' log-distance from the median
# track is multiplied by this before use (1.0 = the comparables' own
# spread, taken as it comes).
SPREAD_SCALE = 1.0


def parse_ts(text):
    """An ISO-8601 UTC timestamp from the case files, as an aware datetime."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def round2(x):
    """Round to 2 decimals, half up; whole numbers come back as ints."""
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if float(value).is_integer() else value


def weighted_quantile(pairs, tau):
    """The tau quantile of values with weights: pairs of (value, weight).

    Linear interpolation on the cumulative weight, the usual weighted
    analogue of the type-7 sample quantile.
    """
    ordered = sorted(pairs)
    total = sum(w for _, w in ordered)
    if total <= 0:
        return ordered[len(ordered) // 2][0]
    target = tau * total
    acc = 0.0
    prev_v, prev_acc = ordered[0][0], 0.0
    for v, w in ordered:
        acc += w
        if acc >= target:
            if acc == prev_acc:
                return v
            frac = (target - prev_acc) / (acc - prev_acc)
            return prev_v + frac * (v - prev_v)
        prev_v, prev_acc = v, acc
    return ordered[-1][0]


def unlock_kind(text):
    """Classify an unlock description: 'rolling', 'global', or None.

    Rolling launches unlock at local midnight country by country; global
    ones at a single worldwide moment. The two draw very different days.
    """
    t = (text or "").lower()
    if "midnight local" in t or "rolling" in t or "local midnight" in t:
        return "rolling"
    if "global unlock" in t or "simultaneous" in t or "single global" in t or "worldwide" in t:
        return "global"
    return None


def hourly_map(points):
    """{(aware datetime at hour start): viewers} from a case's hourly list."""
    out = {}
    for p in points or []:
        try:
            out[parse_ts(p["from"])] = float(p["viewers"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def day_curve(comp, game_name):
    """One comparable's release-day log-ratio curve and its features.

    Returns None when the comparable cannot teach us a day shape: the
    target game's own pre-release history (its 'day' is not a launch), a
    missing or near-zero hour 1, or too few of the day's hours recorded.
    Missing interior hours are filled by log-linear interpolation.
    """
    name = comp.get("name") or ""
    if game_name and name.startswith(game_name):
        return None  # the category's own pre-release history, not a launch
    if "before release" in name.lower():
        return None  # labelled pre-release history, not a launch day
    try:
        start = parse_ts(comp["day_start"])
    except (KeyError, TypeError, ValueError):
        return None
    hours = hourly_map(comp.get("hourly"))
    day = {}
    for h in range(24):
        v = hours.get(start + timedelta(hours=h))
        if v is not None and v > 0:
            day[h] = v
    if 0 not in day or day[0] < 10 or len(day) < 16:
        return None
    # Fill gaps inside the day by log-linear interpolation between the
    # nearest recorded hours (edges take the nearest recorded value).
    known = sorted(day)
    filled = {}
    for h in range(24):
        if h in day:
            filled[h] = day[h]
            continue
        before = max((k for k in known if k < h), default=None)
        after = min((k for k in known if k > h), default=None)
        if before is None:
            filled[h] = day[after]
        elif after is None:
            filled[h] = day[before]
        else:
            f = (h - before) / (after - before)
            filled[h] = math.exp(math.log(day[before]) + f * (math.log(day[after]) - math.log(day[before])))
    base = filled[0]
    log_ratio = {h: math.log(filled[h] / base) for h in range(24)}
    pre = [hours[start - timedelta(hours=k)] for k in range(1, 13)
           if hours.get(start - timedelta(hours=k))]
    surge = math.log(max(pre) / base) if pre else 0.0
    return {
        "log_ratio": log_ratio,
        "scale": math.log(base),
        "surge": surge,
        "kind": unlock_kind(comp.get("unlock")),
    }


def target_features(case):
    """What the case itself shows before the checkpoint.

    Hour 1's print (the level everything is anchored to), the pre-day
    surge over the twelve hours before the day, and the unlock kind from
    the case's notes.
    """
    first = next((k for k in case.get("known", []) if k.get("hour") == 1), None)
    if first is None:
        raise ValueError("case has no hour-1 print")
    p1 = max(1.0, float(first["viewers"]))
    start = parse_ts(case["day_start"])
    hours = hourly_map(case.get("history_hourly"))
    pre = [hours[start - timedelta(hours=k)] for k in range(1, 13)
           if hours.get(start - timedelta(hours=k))]
    surge = math.log(max(pre) / p1) if pre else 0.0
    kind = unlock_kind(case.get("notes")) or unlock_kind(case.get("game", {}).get("name"))
    return p1, math.log(p1), surge, kind


def weight_of(curve, target):
    """How much one comparable's day should count for this target."""
    _, t_scale, t_surge, t_kind = target
    d_scale = (curve["scale"] - t_scale) / SCALE_BW
    d_surge = (curve["surge"] - t_surge) / SURGE_BW
    w = math.exp(-0.5 * (d_scale * d_scale + d_surge * d_surge))
    if t_kind and curve["kind"]:
        w *= CLASS_SAME if curve["kind"] == t_kind else CLASS_DIFF
    return max(w, WEIGHT_FLOOR)


def smooth(values):
    """A light 3-hour moving average over an hour-indexed dict of logs.

    Neighbouring hours of a launch day move together; smoothing the
    quantile tracks keeps one comparable's single odd hour from putting
    a kink in the forecast.
    """
    out = {}
    for h in HOURS:
        acc, tot = 0.0, 0.0
        for k, w in ((h - 1, 0.25), (h, 0.5), (h + 1, 0.25)):
            if k in values:
                acc += w * values[k]
                tot += w
        out[h] = acc / tot if tot else values[h]
    return out


def forecast_case(case):
    """The forecast document for one case: 23 rows, hours 2..24."""
    target = target_features(case)
    p1 = target[0]
    game_name = (case.get("game") or {}).get("name") or ""
    curves = []
    for comp in case.get("comparables") or []:
        curve = day_curve(comp, game_name)
        if curve is not None:
            curve["w"] = weight_of(curve, target)
            curves.append(curve)
    if not curves:
        # No usable comparable: fall back to holding hour 1's level with
        # a widening log spread, the honest shape when nothing is known.
        rows = []
        for row in case["forecast"]:
            h = row["hour"]
            spread = 0.15 * math.sqrt(h - 1)
            q = [round2(p1 * math.exp(z * spread)) for z in (-1.645, -0.674, 0.0, 0.674, 1.645)]
            rows.append({"hour": h, "q": q})
        return {"hours": rows}

    # Per hour and per level, the weighted quantile of the comparables'
    # log ratios; then a light smoothing along each level's track.
    tracks = {tau: {} for tau in LEVELS}
    for row in case["forecast"]:
        h = row["hour"]
        pairs = [(c["log_ratio"][h - 1], c["w"]) for c in curves]
        for tau in LEVELS:
            tracks[tau][h] = weighted_quantile(pairs, tau)
    for tau in LEVELS:
        tracks[tau] = smooth(tracks[tau])

    rows = []
    for row in case["forecast"]:
        h = row["hour"]
        logs = [tracks[tau][h] for tau in LEVELS]
        med = logs[LEVELS.index(0.5)]
        logs = [med + SPREAD_SCALE * (x - med) for x in logs]
        q = []
        for x in logs:
            v = p1 * math.exp(x)
            v = min(max(v, 0.01), 1_000_000_000)
            if q and v < q[-1]:
                v = q[-1]  # quantiles never decrease along the row
            q.append(round2(v))
        rows.append({"hour": h, "q": q})
    return {"hours": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Every case file in name order, so two runs write the same bytes.
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        result = forecast_case(case)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
