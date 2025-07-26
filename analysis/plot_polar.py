#!/usr/bin/env python3
import sys
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def calculate_vmg(speed, angle):
    """Calculate VMG (Velocity Made Good) toward windward"""
    # Convert angle to radians
    angle_rad = np.radians(angle)
    # VMG = speed * cos(angle)
    # For upwind: angle is 0-90 degrees, VMG = speed * cos(angle)
    # For downwind: angle is 90-180 degrees, VMG = speed * cos(180-angle)
    if angle <= 90:
        # Upwind
        vmg = speed * np.cos(angle_rad)
        direction = "upwind"
    else:
        # Downwind
        vmg = speed * np.cos(np.radians(180 - angle))
        direction = "downwind"
    return vmg, direction

def find_optimal_vmg_points(df):
    """Find optimal VMG points for each AWS bin"""
    optimal_points = []
    
    # Group by AWS bin
    aws_bins_raw = df['aws_bin'].dropna().unique()
    aws_bins = sorted(aws_bins_raw, key=lambda x: float(x.split('-')[0]) if '-' in x else float(x))
    
    for aws_bin in aws_bins:
        group = df[df['aws_bin'] == aws_bin]
        group = group.dropna(subset=['twa_bin', 'max'])
        
        if len(group) < 3:
            continue
            
        # Calculate VMG for each angle
        vmg_data = []
        for _, row in group.iterrows():
            angle = row['twa_bin']
            speed = row['max']
            vmg, direction = calculate_vmg(speed, angle)
            vmg_data.append({
                'angle': angle,
                'speed': speed,
                'vmg': vmg,
                'direction': direction
            })
        
        vmg_df = pd.DataFrame(vmg_data)
        
        # Find optimal upwind angle
        upwind = vmg_df[vmg_df['direction'] == 'upwind']
        if len(upwind) > 0:
            optimal_upwind = upwind.loc[upwind['vmg'].idxmax()]
            optimal_points.append({
                'aws_bin': aws_bin,
                'angle': optimal_upwind['angle'],
                'speed': optimal_upwind['speed'],
                'vmg': optimal_upwind['vmg'],
                'direction': 'upwind'
            })
            
        # Find optimal downwind angle
        downwind = vmg_df[vmg_df['direction'] == 'downwind']
        if len(downwind) > 0:
            optimal_downwind = downwind.loc[downwind['vmg'].idxmax()]
            optimal_points.append({
                'aws_bin': aws_bin,
                'angle': optimal_downwind['angle'],
                'speed': optimal_downwind['speed'],
                'vmg': optimal_downwind['vmg'],
                'direction': 'downwind'
            })
    
    return optimal_points

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
    # Sort AWS bins numerically instead of alphabetically
    aws_bins_raw = df['aws_bin'].dropna().unique()
    aws_bins = sorted(aws_bins_raw, key=lambda x: float(x.split('-')[0]) if '-' in x else float(x))
    colors = plt.cm.viridis(np.linspace(0, 1, len(aws_bins)))
    
    # Find optimal VMG points
    optimal_points = find_optimal_vmg_points(df)
    
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
    
    # Add star markers for optimal VMG points
    for point in optimal_points:
        angle_rad = np.radians(point['angle'])
        speed = point['speed']
        direction = point['direction']
        aws_bin = point['aws_bin']
        
        # Find the color for this AWS bin
        aws_bin_index = list(aws_bins).index(aws_bin)
        color = colors[aws_bin_index]
        
        # Use different star styles for upwind vs downwind
        if direction == 'upwind':
            marker = '*'
            markersize = 12
        else:
            marker = 's'
            markersize = 10
        
        ax.plot(angle_rad, speed, marker=marker, markersize=markersize, color=color, 
                markeredgecolor='white', markeredgewidth=1, zorder=10)
    
    ax.set_theta_zero_location('N')
    ax.set_theta_direction(-1)
    ax.set_title("Polar Plot by Apparent Wind Speed (AWS)\n★ = Best Upwind VMG, ■ = Best Downwind VMG", va='bottom')
    ax.set_rlabel_position(225)
    ax.set_xticks(np.radians(np.arange(0, 181, 30)))
    ax.set_xticklabels([f"{d}°" for d in range(0, 181, 30)])
    
    # Create enhanced legend with optimal angles
    legend_elements = []
    
    # Add AWS bin entries
    for aws_bin, color in zip(aws_bins, colors):
        legend_elements.append(plt.Line2D([0], [0], marker='o', color=color, label=aws_bin, linestyle='none'))
    
    # Add optimal VMG entries
    legend_elements.append(plt.Line2D([0], [0], marker='*', color='gray', label='Optimal Upwind Angles:', linestyle='none', markersize=10))
    for point in optimal_points:
        if point['direction'] == 'upwind':
            aws_bin_index = list(aws_bins).index(point['aws_bin'])
            color = colors[aws_bin_index]
            legend_elements.append(plt.Line2D([0], [0], marker='*', color=color, 
                                            label=f"  {point['aws_bin']}: {point['angle']:.0f}° ({point['vmg']:.1f}kt)", 
                                            linestyle='none', markersize=8))
    
    legend_elements.append(plt.Line2D([0], [0], marker='s', color='gray', label='Optimal Downwind Angles:', linestyle='none', markersize=8))
    for point in optimal_points:
        if point['direction'] == 'downwind':
            aws_bin_index = list(aws_bins).index(point['aws_bin'])
            color = colors[aws_bin_index]
            legend_elements.append(plt.Line2D([0], [0], marker='s', color=color, 
                                            label=f"  {point['aws_bin']}: {point['angle']:.0f}° ({point['vmg']:.1f}kt)", 
                                            linestyle='none', markersize=6))
    
    ax.legend(handles=legend_elements, loc='upper left', bbox_to_anchor=(-0.2, 1.0))
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
