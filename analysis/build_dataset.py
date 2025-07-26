#!/usr/bin/env python3
import sys
import argparse
import pandas as pd
import numpy as np
from geopy.distance import geodesic

def fold_twa(twa):
    return twa if twa <= 180 else 360 - twa

def load_gpx_track(gpx_path):
    import xml.etree.ElementTree as ET
    ns = {'default': 'http://www.topografix.com/GPX/1/1'}
    tree = ET.parse(gpx_path)
    root = tree.getroot()

    rows = []
    for trk in root.findall('default:trk', ns):
        for trkseg in trk.findall('default:trkseg', ns):
            for trkpt in trkseg.findall('default:trkpt', ns):
                lat = float(trkpt.attrib['lat'])
                lon = float(trkpt.attrib['lon'])
                time_el = trkpt.find('default:time', ns)
                time = pd.to_datetime(time_el.text) if time_el is not None else None
                rows.append({'datetime': time, 'trk_lat': lat, 'trk_lon': lon})
    return pd.DataFrame(rows)

def main(args):
    df = pd.read_csv(args.nmea, parse_dates=['datetime'])
    df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce')
    
    # Remove rows with NaN datetime values
    df = df.dropna(subset=['datetime'])
    
    df['datetime_round'] = df['datetime'].dt.round('1s')

    # Pivot to align by time
    df_wide = df.sort_values('datetime_round').drop_duplicates(subset=['datetime_round']).copy()

    # Interpolate where necessary
    df_wide = df_wide.set_index('datetime_round')
    df_wide = df_wide[['lat', 'lon', 'sog_knots', 'cog_deg', 'stw_knots', 'awa_deg', 'aws_knots', 'heading_deg']]
    df_wide = df_wide.astype(float)
    df_wide = df_wide.interpolate(method='time', limit_direction='both')
    df_wide = df_wide.reset_index()

    # Compute TWA and tack
    df_wide['twa_raw'] = (df_wide['awa_deg'] + df_wide['heading_deg']) % 360
    df_wide['twa'] = df_wide['twa_raw'].apply(fold_twa)
    df_wide['tack'] = df_wide['awa_deg'].apply(lambda x: 'starboard' if x <= 180 else 'port')

    # Optional engine time filtering
    if args.exclude_engine:
        engine_periods = [
            ("2025-07-26 12:37:00", "2025-07-26 12:56:00"),
            ("2025-07-26 15:17:00", "2025-07-26 15:23:00"),
        ]
        for start, end in engine_periods:
            mask = (df_wide['datetime_round'] >= pd.to_datetime(start)) & (df_wide['datetime_round'] <= pd.to_datetime(end))
            df_wide = df_wide[~mask]

    # Optional race time trimming
    if args.start_time and args.end_time:
        start = pd.to_datetime(args.start_time)
        end = pd.to_datetime(args.end_time)
        df_wide = df_wide[(df_wide['datetime_round'] >= start) & (df_wide['datetime_round'] <= end)]

    # Optional GPX track merge
    if args.track:
        gpx_df = load_gpx_track(args.track)
        df_wide = pd.merge_asof(df_wide.sort_values('datetime_round'),
                                gpx_df.sort_values('datetime'),
                                left_on='datetime_round',
                                right_on='datetime',
                                direction='nearest',
                                tolerance=pd.Timedelta(seconds=2))

    df_wide.to_csv(args.output, index=False)
    print(f"Wrote merged dataset to {args.output}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge NMEA signals and compute TWA/tack.")
    parser.add_argument("nmea", help="CSV from parse_nmea.py")
    parser.add_argument("--track", help="Optional GPX track file")
    parser.add_argument("--output", default="combined.csv", help="Output CSV path")
    parser.add_argument("--exclude-engine", action="store_true", help="Exclude known engine time windows")
    parser.add_argument("--start-time", help="Race start time (UTC, e.g. 2025-07-26T13:15:00)")
    parser.add_argument("--end-time", help="Race end time (UTC, e.g. 2025-07-26T15:15:00)")
    args = parser.parse_args()
    main(args)