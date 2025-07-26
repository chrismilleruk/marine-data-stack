#!/usr/bin/env python3
import sys
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def plot_basic(df, outfile):
    angles = np.radians(df['twa_bin'])
    speeds = df['mean']
    plt.figure(figsize=(8, 8))
    ax = plt.subplot(111, polar=True)
    ax.plot(angles, speeds, marker='o', label='Mean STW')
    ax.set_theta_zero_location('N')
    ax.set_theta_direction(-1)
    ax.set_rlabel_position(225)
    ax.set_title("Polar Plot (Mean STW vs TWA)", va='bottom')
    ax.set_xticks(np.radians(np.arange(0, 181, 30)))
    ax.set_xticklabels([f"{d}°" for d in range(0, 181, 30)])
    plt.tight_layout()
    if outfile:
        plt.savefig(outfile)
    else:
        plt.show()

def plot_by_tack(df, outfile):
    plt.figure(figsize=(8, 8))
    ax = plt.subplot(111, polar=True)
    for tack, group in df.groupby('tack'):
        angles = np.radians(group['twa_bin'])
        speeds = group['mean']
        angle_sign = -1 if tack == 'port' else 1
        ax.plot(angle_sign * angles, speeds, marker='o', label=f"{tack.capitalize()} Tack")
    ax.set_theta_zero_location('N')
    ax.set_theta_direction(-1)
    ax.set_title("Polar Plot by Tack", va='bottom')
    ax.set_rlabel_position(225)
    ax.set_xticks(np.radians(np.arange(0, 181, 30)))
    ax.set_xticklabels([f"{d}°" for d in range(0, 181, 30)])
    ax.legend(loc='upper right', bbox_to_anchor=(1.2, 1.0))
    plt.tight_layout()
    if outfile:
        plt.savefig(outfile)
    else:
        plt.show()

def plot_by_aws(df, outfile):
    plt.figure(figsize=(8, 8))
    ax = plt.subplot(111, polar=True)
    aws_bins = sorted(df['aws_bin'].dropna().unique())
    colors = plt.cm.viridis(np.linspace(0, 1, len(aws_bins)))
    for aws_bin, color in zip(aws_bins, colors):
        group = df[df['aws_bin'] == aws_bin]
        angles = np.radians(group['twa_bin'])
        speeds = group['mean']
        ax.plot(angles, speeds, marker='o', label=str(aws_bin), color=color)
    ax.set_theta_zero_location('N')
    ax.set_theta_direction(-1)
    ax.set_title("Polar Plot by Apparent Wind Speed (AWS)", va='bottom')
    ax.set_rlabel_position(225)
    ax.set_xticks(np.radians(np.arange(0, 181, 30)))
    ax.set_xticklabels([f"{d}°" for d in range(0, 181, 30)])
    ax.legend(loc='upper right', bbox_to_anchor=(1.2, 1.0))
    plt.tight_layout()
    if outfile:
        plt.savefig(outfile)
    else:
        plt.show()

def main(args):
    df = pd.read_csv(args.input)
    if 'tack' in df.columns and args.by_tack:
        plot_by_tack(df, args.output)
    elif 'aws_bin' in df.columns and args.by_aws:
        plot_by_aws(df, args.output)
    else:
        plot_basic(df, args.output)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Plot polar sailing performance from CSV.")
    parser.add_argument("input", help="Polar CSV from analyze_polar.py")
    parser.add_argument("--output", help="Save plot to file instead of displaying")
    parser.add_argument("--by-tack", action="store_true", help="Plot separate lines by tack")
    parser.add_argument("--by-aws", action="store_true", help="Plot separate lines by apparent wind speed")
    args = parser.parse_args()
    main(args)
