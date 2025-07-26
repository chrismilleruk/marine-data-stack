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

def analyze_vmg(df):
    """Analyze VMG for each AWS bin and find optimal angles"""
    results = []
    
    # Group by AWS bin
    aws_bins = sorted(df['aws_bin'].dropna().unique())
    
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
            upwind_angle = optimal_upwind['angle']
            upwind_vmg = optimal_upwind['vmg']
            upwind_speed = optimal_upwind['speed']
        else:
            upwind_angle = upwind_vmg = upwind_speed = None
            
        # Find optimal downwind angle
        downwind = vmg_df[vmg_df['direction'] == 'downwind']
        if len(downwind) > 0:
            optimal_downwind = downwind.loc[downwind['vmg'].idxmax()]
            downwind_angle = optimal_downwind['angle']
            downwind_vmg = optimal_downwind['vmg']
            downwind_speed = optimal_downwind['speed']
        else:
            downwind_angle = downwind_vmg = downwind_speed = None
            
        results.append({
            'aws_bin': aws_bin,
            'upwind_angle': upwind_angle,
            'upwind_vmg': upwind_vmg,
            'upwind_speed': upwind_speed,
            'downwind_angle': downwind_angle,
            'downwind_vmg': downwind_vmg,
            'downwind_speed': downwind_speed,
            'data_points': len(group)
        })
    
    return pd.DataFrame(results)

def plot_vmg_analysis(df, output_file=None):
    """Plot VMG analysis showing optimal angles"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
    
    aws_bins = sorted(df['aws_bin'].dropna().unique())
    colors = plt.cm.viridis(np.linspace(0, 1, len(aws_bins)))
    
    # Plot 1: VMG vs Angle for each AWS bin
    for aws_bin, color in zip(aws_bins, colors):
        group = df[df['aws_bin'] == aws_bin]
        group = group.dropna(subset=['twa_bin', 'max'])
        
        if len(group) < 3:
            continue
            
        # Calculate VMG for all angles
        angles = []
        vmgs = []
        directions = []
        
        for _, row in group.iterrows():
            angle = row['twa_bin']
            speed = row['max']
            vmg, direction = calculate_vmg(speed, angle)
            angles.append(angle)
            vmgs.append(vmg)
            directions.append(direction)
        
        # Plot upwind and downwind separately
        upwind_mask = [d == 'upwind' for d in directions]
        downwind_mask = [d == 'downwind' for d in directions]
        
        if any(upwind_mask):
            ax1.plot([angles[i] for i in range(len(angles)) if upwind_mask[i]], 
                    [vmgs[i] for i in range(len(vmgs)) if upwind_mask[i]], 
                    'o-', color=color, label=f'{aws_bin} (upwind)', alpha=0.7)
        
        if any(downwind_mask):
            ax2.plot([angles[i] for i in range(len(angles)) if downwind_mask[i]], 
                    [vmgs[i] for i in range(len(vmgs)) if downwind_mask[i]], 
                    'o-', color=color, label=f'{aws_bin} (downwind)', alpha=0.7)
    
    ax1.set_xlabel('True Wind Angle (degrees)')
    ax1.set_ylabel('VMG (knots)')
    ax1.set_title('Upwind VMG Analysis')
    ax1.grid(True, alpha=0.3)
    ax1.legend()
    
    ax2.set_xlabel('True Wind Angle (degrees)')
    ax2.set_ylabel('VMG (knots)')
    ax2.set_title('Downwind VMG Analysis')
    ax2.grid(True, alpha=0.3)
    ax2.legend()
    
    plt.tight_layout()
    
    if output_file:
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        print(f"VMG analysis plot saved to {output_file}")
    else:
        plt.show()

def main(args):
    df = pd.read_csv(args.input)
    
    # Analyze VMG
    vmg_results = analyze_vmg(df)
    
    # Print results
    print("VMG Analysis Results:")
    print("=" * 80)
    for _, row in vmg_results.iterrows():
        print(f"\nAWS Bin: {row['aws_bin']} knots")
        print(f"Data points: {row['data_points']}")
        
        if row['upwind_angle'] is not None:
            print(f"  Optimal Upwind: {row['upwind_angle']:.1f}° TWA")
            print(f"    Speed: {row['upwind_speed']:.1f} knots")
            print(f"    VMG: {row['upwind_vmg']:.1f} knots")
        else:
            print("  No upwind data available")
            
        if row['downwind_angle'] is not None:
            print(f"  Optimal Downwind: {row['downwind_angle']:.1f}° TWA")
            print(f"    Speed: {row['downwind_speed']:.1f} knots")
            print(f"    VMG: {row['downwind_vmg']:.1f} knots")
        else:
            print("  No downwind data available")
    
    # Save detailed results
    if args.output:
        vmg_results.to_csv(args.output, index=False)
        print(f"\nDetailed VMG results saved to {args.output}")
    
    # Create plot
    if args.plot:
        plot_vmg_analysis(df, args.plot)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze VMG and find optimal sailing angles.")
    parser.add_argument("input", help="Polar CSV from analyze_polar.py")
    parser.add_argument("--output", help="Save detailed VMG results to CSV")
    parser.add_argument("--plot", help="Save VMG analysis plot to file")
    args = parser.parse_args()
    main(args) 