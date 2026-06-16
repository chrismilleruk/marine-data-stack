#!/usr/bin/env python3
"""Export race-day NMEA tracks from SignalK raw logs for the replay test rig."""

import json
import glob
import os
from datetime import datetime, timedelta
from collections import Counter

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(ANALYSIS_DIR, "logs-2025")
PHASES_FILE = os.path.join(ANALYSIS_DIR, "race_phases.json")
OUTPUT_DIR = os.path.join(os.path.dirname(ANALYSIS_DIR), "nmea-test-rig")
TRACKS_DIR = os.path.join(OUTPUT_DIR, "tracks")


def parse_utc(s):
    """Parse 'YYYY-MM-DD HH:MM:SS' to datetime."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def ts_ms_to_dt(ts_ms):
    """Convert unix millisecond timestamp to datetime."""
    return datetime.utcfromtimestamp(ts_ms / 1000)


def get_sentence_type(line):
    """Extract NMEA sentence type from a log line (e.g. 'GNRMC', 'IIMWV')."""
    parts = line.split(";")
    if len(parts) < 3:
        return None
    sentence = parts[2].strip()
    if not sentence:
        return None
    # Handle standard ($) and proprietary (!) prefixes
    if sentence[0] in ("$", "!"):
        tag = sentence[1:].split(",")[0].split("*")[0]
    else:
        tag = sentence.split(",")[0].split("*")[0]
    return tag


def export_race_day(race, logs_dir, tracks_dir):
    """Export a single race day's NMEA data to a .nmea file.

    Returns metadata dict or None if no data.
    """
    date_str = race["date"]
    # Fallback chain: motor_out_start → race_start → data_start
    start_key = race.get("motor_out_start") or race.get("race_start") or race.get("data_start")
    if not start_key:
        print(f"  SKIP {date_str}: no start timestamp found")
        return None
    start_utc = parse_utc(start_key)
    end_utc = parse_utc(race["data_end"])

    # Convert to timestamp range in ms
    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)

    # Find all log files for this date (and possibly the next hour into next day)
    # Logs are named skserver-raw_YYYY-MM-DDTHH.log
    log_files = sorted(glob.glob(os.path.join(logs_dir, f"*{date_str}*.log")))

    # Also check if data_end spills into next day
    end_date_str = end_utc.strftime("%Y-%m-%d")
    if end_date_str != date_str:
        log_files += sorted(glob.glob(os.path.join(logs_dir, f"*{end_date_str}*.log")))

    if not log_files:
        print(f"  SKIP {date_str}: no log files found")
        return None

    lines_out = []
    sentence_types = Counter()

    for lf in log_files:
        with open(lf, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(";")
                if len(parts) < 3:
                    continue
                try:
                    ts_ms = int(parts[0])
                except (ValueError, IndexError):
                    continue

                if ts_ms < start_ms or ts_ms > end_ms:
                    continue

                lines_out.append(line)
                stype = get_sentence_type(line)
                if stype:
                    sentence_types[stype] += 1

    if not lines_out:
        print(f"  SKIP {date_str}: no lines in time range")
        return None

    # Sort by timestamp (should be mostly sorted, but log files may overlap)
    lines_out.sort(key=lambda l: int(l.split(";")[0]))

    # Write track file
    track_file = os.path.join(tracks_dir, f"{date_str}.nmea")
    with open(track_file, "w") as f:
        f.write("\n".join(lines_out))
        f.write("\n")

    # Compute metadata
    first_ts = int(lines_out[0].split(";")[0])
    last_ts = int(lines_out[-1].split(";")[0])
    duration_sec = (last_ts - first_ts) / 1000

    meta = {
        "file": f"{date_str}.nmea",
        "date": date_str,
        "day": race["day"],
        "start_area": race["start_area"],
        "gun_time_utc": race.get("gun_time_utc", ""),
        "motor_out_start": race.get("motor_out_start", ""),
        "race_start": race.get("race_start", ""),
        "race_end": race.get("race_end", ""),
        "race_duration_min": race.get("race_duration_min", 0),
        "home_destination": race.get("home_destination", ""),
        "sentence_count": len(lines_out),
        "duration_sec": round(duration_sec, 1),
        "first_timestamp_ms": first_ts,
        "last_timestamp_ms": last_ts,
        "sentences_available": sorted(sentence_types.keys()),
        "sentence_counts": dict(sentence_types.most_common()),
    }

    size_kb = os.path.getsize(track_file) / 1024
    print(
        f"  {date_str}: {len(lines_out):,} lines, "
        f"{duration_sec/60:.0f} min, "
        f"{size_kb:.0f} KB, "
        f"{len(sentence_types)} sentence types"
    )
    return meta


def main():
    os.makedirs(TRACKS_DIR, exist_ok=True)

    with open(PHASES_FILE) as f:
        phases = json.load(f)

    raced = [r for r in phases if r["status"] == "raced"]
    print(f"Found {len(raced)} raced days to export\n")

    tracks = []
    for race in raced:
        meta = export_race_day(race, LOGS_DIR, TRACKS_DIR)
        if meta:
            tracks.append(meta)

    # Write manifest
    manifest = {
        "boat": "Blacksheep",
        "class": "Cruiser 3",
        "venue": "Dublin Bay",
        "season": 2025,
        "track_count": len(tracks),
        "total_sentences": sum(t["sentence_count"] for t in tracks),
        "total_duration_min": round(sum(t["duration_sec"] for t in tracks) / 60, 1),
        "tracks": tracks,
    }

    manifest_path = os.path.join(OUTPUT_DIR, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nExported {len(tracks)} tracks to {TRACKS_DIR}")
    print(f"Manifest written to {manifest_path}")
    print(f"Total: {manifest['total_sentences']:,} sentences, {manifest['total_duration_min']:.0f} min")


if __name__ == "__main__":
    main()
