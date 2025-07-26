#!/usr/bin/env python3
import sys
import csv
from datetime import datetime

def parse_lat_lon(lat_str, ns, lon_str, ew):
    lat_deg = float(lat_str[:2])
    lat_min = float(lat_str[2:])
    lat = lat_deg + (lat_min / 60.0)
    if ns == 'S':
        lat = -lat

    lon_deg = float(lon_str[:3])
    lon_min = float(lon_str[3:])
    lon = lon_deg + (lon_min / 60.0)
    if ew == 'W':
        lon = -lon

    return lat, lon

def main(filenames):
    writer = csv.DictWriter(sys.stdout, fieldnames=[
        'datetime', 'lat', 'lon', 'sog_knots', 'cog_deg',
        'stw_knots', 'awa_deg', 'aws_knots', 'awa_type',
        'heading_deg'
    ])
    writer.writeheader()

    for fname in filenames:
        with open(fname, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                parts = line.strip().split(";")
                if len(parts) != 3 or parts[1] != "N" or not parts[2].startswith("$"):
                    continue
                ts = datetime.utcfromtimestamp(int(parts[0]) / 1000)
                sentence = parts[2]
                fields = sentence.split(",")

                row = {'datetime': ts}

                if sentence.startswith("$GNRMC") and len(fields) > 9 and fields[2] == "A":
                    try:
                        lat, lon = parse_lat_lon(fields[3], fields[4], fields[5], fields[6])
                        row.update({
                            'lat': lat,
                            'lon': lon,
                            'sog_knots': float(fields[7]),
                            'cog_deg': float(fields[8])
                        })
                    except:
                        continue

                elif sentence.startswith("$IIVHW") and len(fields) >= 6:
                    try:
                        row['stw_knots'] = float(fields[5])
                    except:
                        continue

                elif sentence.startswith("$IIMWV") and len(fields) >= 6 and fields[5].startswith("A"):
                    try:
                        row['awa_deg'] = float(fields[1])
                        row['awa_type'] = fields[2]
                        row['aws_knots'] = float(fields[3])
                    except:
                        continue

                elif sentence.startswith("$IIHDG") and len(fields) >= 2:
                    try:
                        row['heading_deg'] = float(fields[1])
                    except:
                        continue

                if len(row) > 1:
                    writer.writerow(row)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parse_nmea.py log1.log [log2.log ...]", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1:])