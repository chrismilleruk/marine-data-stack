#!/usr/bin/env python3
import sys
import argparse
import pandas as pd

def main(args):
    df = pd.read_csv(args.input)

    # Bin TWA
    df['twa_bin'] = (df['twa'] / args.twa_bin_size).round() * args.twa_bin_size

    # Bin AWS if requested
    if args.aws_bin_size:
        aws_bins = list(range(0, int(df['aws_knots'].max()) + args.aws_bin_size, args.aws_bin_size))
        labels = [f"{aws_bins[i]}-{aws_bins[i+1]}" for i in range(len(aws_bins)-1)]
        df['aws_bin'] = pd.cut(df['aws_knots'], bins=aws_bins, labels=labels)

    # Group and aggregate
    group_cols = ['twa_bin']
    if args.by_tack:
        group_cols.append('tack')
    if args.aws_bin_size:
        group_cols.append('aws_bin')

    grouped = df.groupby(group_cols, observed=False)['stw_knots'].agg(['min', 'max', 'mean', 'count']).reset_index()

    grouped.to_csv(args.output, index=False)
    print(f"Wrote polar stats to {args.output}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate polar stats from sailing dataset.")
    parser.add_argument("input", help="Input CSV from build_dataset.py")
    parser.add_argument("--output", default="polar.csv", help="Output CSV file")
    parser.add_argument("--twa-bin-size", type=int, default=5, help="TWA bin size in degrees")
    parser.add_argument("--by-tack", action="store_true", help="Include port/starboard tack split")
    parser.add_argument("--aws-bin-size", type=int, help="Bin AWS (Apparent Wind Speed) in knots")
    args = parser.parse_args()
    main(args)
