#!/usr/bin/env python3
"""
Engine detection for race days.

Strategy:
  - Motor OUT: from first movement at home until race gun time
  - Racing: from gun time until motor home begins
  - Motor HOME: work BACKWARDS from arrival at home — find the point where
    the boat was last clearly racing (changing course, far from home) and
    the transition to a direct track home began.

The key insight for motor-home detection is to work backwards from the
known endpoint (boat at rest at home) rather than forwards from racing.
"""

import os
import sys
import json
import glob
from datetime import datetime, timedelta
from math import radians, sin, cos, sqrt, atan2

import pandas as pd
import numpy as np


HOME_MOORING = (53.29988, -6.12587)
HOME_NYC     = (53.29402, -6.12996)
HOME_RADIUS_M = 200  # within this = "at home"
RACING_MIN_DIST_M = 400  # must be at least this far from home to be "racing"


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def nearest_home(lat, lon):
    d1 = haversine_m(lat, lon, *HOME_MOORING)
    d2 = haversine_m(lat, lon, *HOME_NYC)
    if d1 <= d2:
        return 'mooring', d1
    else:
        return 'nyc', d2


def parse_gnrmc(line):
    parts = line.split(';')
    if len(parts) < 3:
        return None
    try:
        ts_ms = int(parts[0])
    except:
        return None
    sentence = parts[2]
    if not sentence.startswith('$GNRMC'):
        return None
    if '\ufffd' in sentence:
        return None
    fields = sentence.split(',')
    if len(fields) < 9 or fields[2] != 'A':
        return None
    try:
        lat_raw = float(fields[3])
        lat = int(lat_raw / 100) + (lat_raw % 100) / 60
        if fields[4] == 'S':
            lat = -lat
        lon_raw = float(fields[5])
        lon = int(lon_raw / 100) + (lon_raw % 100) / 60
        if fields[6] == 'W':
            lon = -lon
        sog = float(fields[7]) if fields[7] else 0
        cog = float(fields[8]) if fields[8] else 0
    except:
        return None
    if lat < 53.25 or lat > 53.40 or lon > -5.9 or lon < -6.25 or sog > 15:
        return None
    return (datetime.utcfromtimestamp(ts_ms / 1000), lat, lon, sog, cog)


def load_day_data(log_dir, date_str):
    """Load all GNRMC data for a given date, return DataFrame sorted by time."""
    pattern = os.path.join(log_dir, f"*{date_str}*.log")
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()

    rows = []
    for f in files:
        with open(f, 'r', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                result = parse_gnrmc(line)
                if result:
                    rows.append(result)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=['datetime', 'lat', 'lon', 'sog', 'cog'])
    df = df.sort_values('datetime').reset_index(drop=True)

    # Add distance to nearest home
    home_info = df.apply(lambda r: nearest_home(r['lat'], r['lon']), axis=1)
    df['home_name'] = home_info.apply(lambda x: x[0])
    df['home_dist_m'] = home_info.apply(lambda x: x[1])

    return df


def detect_motor_home_backwards(df, gun_time_utc):
    """
    Work backwards from home arrival to find where the direct homeward track began.

    Strategy:
    1. Find FIRST arrival at home after racing (within HOME_RADIUS_M, SOG < 1.5kt),
       but only after the boat has been far from home (>RACING_MIN_DIST_M).
    2. From that arrival point, walk backwards through the data looking for where
       the boat was last moving AWAY from home or holding steady distance (= racing).
       The point where distance starts monotonically decreasing = motor home start.

    Returns: (motor_home_start_dt, arrived_home_dt) or None
    """
    if df.empty:
        return None

    # Only look at data AFTER the gun
    post_gun = df[df['datetime'] >= gun_time_utc].copy().reset_index(drop=True)
    if post_gun.empty:
        return None

    # Step 1: Find first SUSTAINED arrival home AFTER the boat has been far away racing.
    # The boat must first reach RACING_MIN_DIST_M, then come back within HOME_RADIUS_M
    # and STAY there for at least MIN_HOME_DWELL seconds (to filter GPS glitches).
    MIN_HOME_DWELL_S = 30  # must be at home for 30+ seconds to count

    was_far = False
    arrived_idx = None
    home_entry_idx = None
    for i in range(len(post_gun)):
        row = post_gun.iloc[i]
        if row['home_dist_m'] > RACING_MIN_DIST_M:
            was_far = True
            home_entry_idx = None  # reset if boat goes far again
        if was_far and row['home_dist_m'] < HOME_RADIUS_M and row['sog'] < 2.0:
            if home_entry_idx is None:
                home_entry_idx = i
            else:
                # Check dwell time
                dwell = (row['datetime'] - post_gun.iloc[home_entry_idx]['datetime']).total_seconds()
                if dwell >= MIN_HOME_DWELL_S:
                    arrived_idx = home_entry_idx
                    break
        else:
            home_entry_idx = None  # reset — left home radius or speeding

    if arrived_idx is None:
        # Boat never came home after racing
        return None

    arrived_time = post_gun.iloc[arrived_idx]['datetime']

    # Step 2: Get the segment from gun to arrival, resample to 2-minute buckets.
    # Using larger windows makes the backwards walk much more robust.
    segment = post_gun.iloc[:arrived_idx + 1].copy()
    if len(segment) < 10:
        return (gun_time_utc, arrived_time)

    numeric_cols = ['lat', 'lon', 'sog', 'cog', 'home_dist_m']
    segment = segment.set_index('datetime')

    # Resample: use mean for most cols, std of COG for stability detection
    seg_2m = segment[numeric_cols].resample('2min').agg({
        'lat': 'mean', 'lon': 'mean', 'sog': 'mean',
        'cog': ['mean', 'std'],
        'home_dist_m': 'mean'
    }).dropna(subset=[('lat', 'mean')]).reset_index()

    # Flatten multi-level columns
    seg_2m.columns = ['datetime', 'lat', 'lon', 'sog', 'cog_mean', 'cog_std', 'home_dist_m']

    if len(seg_2m) < 3:
        return (gun_time_utc, arrived_time)

    dists = seg_2m['home_dist_m'].values
    times = seg_2m['datetime'].values
    cog_stds = seg_2m['cog_std'].values

    # Step 3: Walk backwards from arrival.
    # Motor home signature in 2-min buckets:
    #   - Distance decreasing by significant amounts (>50m per 2min at motoring speed)
    #   - COG standard deviation is LOW (< ~25°, straight-line course)
    # Racing signature:
    #   - Distance may increase or hold
    #   - COG standard deviation is HIGH (tacking, rounding marks)
    #
    # Walk back: each 2-min bucket where dist[i] > dist[i+1] (boat was further away
    # = closing on home) is part of motor-home approach. When we find dist NOT
    # decreasing, we've hit the racing boundary.

    motor_home_start_idx = len(dists) - 1

    for i in range(len(dists) - 2, -1, -1):
        dist_change = dists[i] - dists[i + 1]  # positive = boat was further away (closing)

        # Is the boat closing on home AND course is stable?
        is_closing = dist_change > 0  # was further away at time i
        cog_stable = cog_stds[i + 1] < 30 if not np.isnan(cog_stds[i + 1]) else True

        if is_closing and cog_stable:
            motor_home_start_idx = i
        else:
            # Not closing or course unstable — this is racing
            break

    motor_home_start_time = pd.Timestamp(times[motor_home_start_idx])

    # Sanity: motor home should be well after gun (at least 30 min of racing)
    min_race_time = gun_time_utc + timedelta(minutes=30)
    if motor_home_start_time < min_race_time:
        # Something went wrong — possibly the distance profile is noisy
        # Fall back: find the maximum distance point after gun, use that
        max_dist_idx = np.argmax(dists)
        motor_home_start_time = pd.Timestamp(times[max_dist_idx])
        if motor_home_start_time < min_race_time:
            motor_home_start_time = min_race_time

    return (motor_home_start_time, arrived_time)


def detect_motor_out(df, gun_time_utc):
    """
    Detect motor-out phase: from first movement at home to gun time.

    Returns: (depart_time, gun_time) or None
    """
    if df.empty:
        return None

    pre_gun = df[df['datetime'] < gun_time_utc].copy()
    if pre_gun.empty:
        return None

    # Find first movement: SOG > 1kt while near home
    depart_time = None
    for _, row in pre_gun.iterrows():
        if row['home_dist_m'] < HOME_RADIUS_M * 3 and row['sog'] > 1.0:
            depart_time = row['datetime']
            break

    if depart_time is None:
        # Already away from home when logs start
        depart_time = pre_gun.iloc[0]['datetime']

    return (depart_time, gun_time_utc)


def detect_race_phases(df, gun_time_utc):
    """
    Returns dict with:
      motor_out: (start, end)   — engine on, leaving home
      racing: (start, end)      — engine off, racing
      motor_home: (start, end)  — engine on, returning home
    """
    result = {
        'motor_out': None,
        'racing': None,
        'motor_home': None,
        'arrived_home': None,
        'departure_home': None,
    }

    # Motor out
    mo = detect_motor_out(df, gun_time_utc)
    if mo:
        result['motor_out'] = mo
        result['departure_home'] = mo[0]

    # Motor home (work backwards)
    mh = detect_motor_home_backwards(df, gun_time_utc)
    if mh:
        result['motor_home'] = mh
        result['arrived_home'] = mh[1]
        # Racing = gun to motor_home_start
        result['racing'] = (gun_time_utc, mh[0])
    else:
        # No motor home detected — boat didn't come home in data
        # Racing = gun to end of data
        last_time = df.iloc[-1]['datetime']
        result['racing'] = (gun_time_utc, last_time)

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Detect engine phases for race days")
    parser.add_argument("--log-dir", default="logs-2025", help="Directory with log files")
    parser.add_argument("--schedule", default="race_schedule_cr3.json", help="Race schedule JSON")
    parser.add_argument("--output", default="race_phases.json", help="Output JSON")
    parser.add_argument("--date", help="Process single date (YYYY-MM-DD)")
    parser.add_argument("--verbose", action="store_true", help="Print details")
    args = parser.parse_args()

    with open(args.schedule) as f:
        schedule = json.load(f)

    results = []

    races_to_process = schedule['races']
    if args.date:
        races_to_process = [r for r in races_to_process if r['date'] == args.date]

    for race in races_to_process:
        date_str = race['date']
        start_time_local = race['start_time']  # IST (UTC+1)
        start_area = race['start_area']

        # Convert IST start time to UTC
        gun_time_ist = datetime.strptime(f"{date_str} {start_time_local}", "%Y-%m-%d %H:%M")
        gun_time_utc = gun_time_ist - timedelta(hours=1)  # IST = UTC+1

        if args.verbose:
            print(f"\n{'='*60}")
            print(f"Date: {date_str} ({race['day']}), Start area: {start_area}")
            print(f"Gun time: {start_time_local} IST = {gun_time_utc.strftime('%H:%M')} UTC")

        # Load data
        df = load_day_data(args.log_dir, date_str)
        if df.empty:
            if args.verbose:
                print(f"  No log data for {date_str}")
            results.append({
                'date': date_str,
                'day': race['day'],
                'start_area': start_area,
                'gun_time_ist': start_time_local,
                'status': 'no_data'
            })
            continue

        # Check if boat was even sailing (not at home all day)
        max_dist = df['home_dist_m'].max()
        if max_dist < HOME_RADIUS_M:
            if args.verbose:
                print(f"  Boat stayed at home all day (max dist: {max_dist:.0f}m)")
            results.append({
                'date': date_str,
                'day': race['day'],
                'start_area': start_area,
                'gun_time_ist': start_time_local,
                'status': 'at_home'
            })
            continue

        # Check if data covers the gun time
        data_end = df.iloc[-1]['datetime']
        if data_end < gun_time_utc:
            if args.verbose:
                print(f"  Data ends at {data_end.strftime('%H:%M:%S')} before gun at {gun_time_utc.strftime('%H:%M:%S')} UTC — incomplete")
            results.append({
                'date': date_str,
                'day': race['day'],
                'start_area': start_area,
                'gun_time_ist': start_time_local,
                'status': 'incomplete_data',
                'data_start': df.iloc[0]['datetime'].strftime('%Y-%m-%d %H:%M:%S'),
                'data_end': data_end.strftime('%Y-%m-%d %H:%M:%S'),
            })
            continue

        # Detect phases
        phases = detect_race_phases(df, gun_time_utc)

        race_duration = None
        if phases['racing']:
            rs, re = phases['racing']
            race_duration = (re - rs).total_seconds()

        entry = {
            'date': date_str,
            'day': race['day'],
            'start_area': start_area,
            'gun_time_ist': start_time_local,
            'gun_time_utc': gun_time_utc.strftime('%Y-%m-%d %H:%M:%S'),
            'status': 'raced',
            'data_start': df.iloc[0]['datetime'].strftime('%Y-%m-%d %H:%M:%S'),
            'data_end': df.iloc[-1]['datetime'].strftime('%Y-%m-%d %H:%M:%S'),
        }

        if phases['motor_out']:
            entry['motor_out_start'] = phases['motor_out'][0].strftime('%Y-%m-%d %H:%M:%S')
            entry['motor_out_end'] = phases['motor_out'][1].strftime('%Y-%m-%d %H:%M:%S')

        if phases['racing']:
            entry['race_start'] = phases['racing'][0].strftime('%Y-%m-%d %H:%M:%S')
            entry['race_end'] = phases['racing'][1].strftime('%Y-%m-%d %H:%M:%S')
            entry['race_duration_min'] = round(race_duration / 60, 1)

        if phases['motor_home']:
            entry['motor_home_start'] = phases['motor_home'][0].strftime('%Y-%m-%d %H:%M:%S')
            entry['motor_home_end'] = phases['motor_home'][1].strftime('%Y-%m-%d %H:%M:%S')

        if phases['arrived_home']:
            entry['arrived_home'] = phases['arrived_home'].strftime('%Y-%m-%d %H:%M:%S')
            near_arrival = df.iloc[(df['datetime'] - phases['arrived_home']).abs().argsort()[:1]]
            if not near_arrival.empty:
                entry['home_destination'] = near_arrival.iloc[0]['home_name']

        results.append(entry)

        if args.verbose:
            print(f"  Data: {entry['data_start']} to {entry['data_end']}")
            if 'motor_out_start' in entry:
                print(f"  Motor out:  {entry['motor_out_start']} → {entry['motor_out_end']}")
            if 'race_start' in entry:
                print(f"  Racing:     {entry['race_start']} → {entry['race_end']} ({entry['race_duration_min']} min)")
            if 'motor_home_start' in entry:
                print(f"  Motor home: {entry['motor_home_start']} → {entry['motor_home_end']}")

    # Save results
    with open(args.output, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote {len(results)} race phase records to {args.output}")

    # Summary
    raced = [r for r in results if r['status'] == 'raced']
    at_home = [r for r in results if r['status'] == 'at_home']
    no_data = [r for r in results if r['status'] == 'no_data']
    print(f"  Raced: {len(raced)}, At home: {len(at_home)}, No data: {len(no_data)}")

    if raced:
        durations = [r['race_duration_min'] for r in raced if 'race_duration_min' in r]
        if durations:
            print(f"  Race durations: min={min(durations):.0f}min, max={max(durations):.0f}min, "
                  f"avg={np.mean(durations):.0f}min, median={np.median(durations):.0f}min")


if __name__ == "__main__":
    main()
