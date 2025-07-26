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
        # Remove rows with NaN values for interpolation
        group = group.dropna(subset=['twa_bin', 'mean'])
        if len(group) < 2:
            continue
            
        angles = np.radians(group['twa_bin'].values)
        speeds = group['max'].values  # Use max speeds for optimistic polar
        
        # Sort by angle for proper interpolation
        sort_idx = np.argsort(angles)
        angles_sorted = angles[sort_idx]
        speeds_sorted = speeds[sort_idx]
        
        # Create smooth interpolation that follows higher speeds
        if len(angles_sorted) >= 3:
            # Create finer angle grid for smooth interpolation
            angle_fine = np.linspace(angles_sorted.min(), angles_sorted.max(), 100)
            
            # For each angle bin, find the higher speed cluster
            # Use a simple approach: take the upper quartile of speeds for each angle
            filtered_angles = []
            filtered_speeds = []
            
            for angle in np.unique(angles_sorted):
                # Find all speeds for this angle
                angle_mask = angles_sorted == angle
                angle_speeds = speeds_sorted[angle_mask]
                
                if len(angle_speeds) > 0:
                    # Use the higher speeds (upper 50% or just the max if few points)
                    if len(angle_speeds) >= 4:
                        threshold = np.percentile(angle_speeds, 50)  # Use upper half
                    else:
                        threshold = np.percentile(angle_speeds, 75)  # Use upper quarter
                    
                    # Take the best speeds for this angle
                    good_speeds = angle_speeds[angle_speeds >= threshold]
                    if len(good_speeds) > 0:
                        filtered_angles.append(angle)
                        filtered_speeds.append(np.mean(good_speeds))  # Average of the good speeds
            
            if len(filtered_angles) >= 3:
                try:
                    # Fit a polynomial to the filtered (higher speed) data
                    degree = min(2, len(filtered_angles)-1)
                    coeffs = np.polyfit(filtered_angles, filtered_speeds, degree)
                    speed_fine = np.polyval(coeffs, angle_fine)
                    # Plot smooth line
                    ax.plot(angle_fine, speed_fine, color=color, linewidth=2, alpha=0.8)
                except:
                    # Fallback to simple linear interpolation
                    ax.plot(filtered_angles, filtered_speeds, color=color, linewidth=2, alpha=0.8)
            else:
                # If not enough filtered points, use original approach
                try:
                    degree = min(2, len(angles_sorted)-1)
                    coeffs = np.polyfit(angles_sorted, speeds_sorted, degree)
                    speed_fine = np.polyval(coeffs, angle_fine)
                    ax.plot(angle_fine, speed_fine, color=color, linewidth=2, alpha=0.8)
                except:
                    ax.plot(angles_sorted, speeds_sorted, color=color, linewidth=2, alpha=0.8)
        
        # Plot original data points only (no straight lines)
        ax.plot(angles_sorted, speeds_sorted, marker='o', markersize=4, color=color, label=str(aws_bin), linestyle='none')
    
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
