#!/usr/bin/env python3
"""Build a smoothed polar table from the 3D grid data.

Computes P96 STW for each (TWA, TWS) cell, identifies gaps and noisy cells,
then fills missing values using 2D interpolation and physical constraints.

Outputs polar_smooth.json with clean curves suitable for navigation use.
"""

import json
import os
import numpy as np

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
GRID_FILE = os.path.join(ANALYSIS_DIR, "polar_grid_3d.json")
OUTPUT_FILE = os.path.join(ANALYSIS_DIR, "polar_smooth.json")

PERCENTILE = 96
MIN_MINUTES = 1.0  # minimum minutes of data to trust a cell

def load_grid():
    with open(GRID_FILE) as f:
        return json.load(f)

def compute_p96_table(data):
    """Compute P96 STW for each (TWA_index, TWS_index) cell."""
    n_twa = len(data["twa_labels"])
    n_tws = len(data["tws_labels"])
    n_stw = len(data["stw_labels"])

    # Result: P96 STW value, or NaN if insufficient data
    p96 = np.full((n_twa, n_tws), np.nan)
    minutes = np.full((n_twa, n_tws), 0.0)

    for ai in range(n_twa):
        twa_label = data["twa_labels"][ai]
        for ti in range(n_tws):
            tws_label = data["tws_labels"][ti]
            # Build distribution for this (TWA, TWS) cell
            dist = []
            total_min = 0.0
            for si in range(n_stw):
                stw_label = data["stw_labels"][si]
                key = f"{twa_label}|{stw_label}|{tws_label}"
                mins = data["grid"].get(key, 0)
                dist.append(mins)
                total_min += mins

            minutes[ai, ti] = total_min
            if total_min < MIN_MINUTES:
                continue

            # Compute P96 via CDF walk
            pct = PERCENTILE
            if pct >= 100:
                for si in range(n_stw - 1, -1, -1):
                    if dist[si] > 0:
                        p96[ai, ti] = data["stw_edges"][si + 1]
                        break
            else:
                target = (pct / 100.0) * total_min
                cumul = 0.0
                for si in range(n_stw):
                    if dist[si] == 0:
                        continue
                    prev_cumul = cumul
                    cumul += dist[si]
                    if cumul >= target:
                        frac = (target - prev_cumul) / dist[si]
                        lo = data["stw_edges"][si]
                        hi = data["stw_edges"][si + 1]
                        p96[ai, ti] = lo + frac * (hi - lo)
                        break

    return p96, minutes

def print_coverage(data, p96, minutes):
    """Print a coverage summary."""
    n_twa = len(data["twa_labels"])
    n_tws = len(data["tws_labels"])

    print(f"\nP{PERCENTILE} Coverage Map (minutes ≥ {MIN_MINUTES})")
    print(f"{'TWA':<10}", end="")
    for tws in data["tws_labels"]:
        print(f"{tws:>7}", end="")
    print()

    for ai in range(n_twa):
        print(f"{data['twa_labels'][ai]:<10}", end="")
        for ti in range(n_tws):
            if np.isnan(p96[ai, ti]):
                print(f"{'—':>7}", end="")
            else:
                print(f"{p96[ai, ti]:>7.1f}", end="")
        print(f"  ({minutes[ai].sum():.0f} min)")

    # Summary
    total = n_twa * n_tws
    filled = np.count_nonzero(~np.isnan(p96))
    print(f"\n{filled}/{total} cells have data ({filled/total*100:.0f}%)")

def interpolate_polar(data, p96, minutes):
    """
    Fill missing cells using physical constraints and 2D interpolation.

    Strategy:
    1. TWA 0-20° at low TWS: expect very low speeds (close-hauled, nearly head-to-wind)
    2. For each TWS bucket, interpolate along TWA axis where gaps exist
    3. For each TWA bucket, interpolate along TWS axis where gaps exist
    4. Use iterative 2D weighted averaging for remaining gaps
    5. Apply physical constraints:
       - STW should generally increase with TWS (for same TWA)
       - STW should increase from TWA 0 to ~100-120° then may decrease
       - STW at TWA 0-10° should be near zero (in irons)
    """
    n_twa = len(data["twa_labels"])
    n_tws = len(data["tws_labels"])

    # TWA mid-points for each bin
    twa_mids = [(data["twa_edges"][i] + data["twa_edges"][i + 1]) / 2
                for i in range(n_twa)]
    # TWS mid-points
    tws_mids = [(data["tws_edges"][i] + data["tws_edges"][i + 1]) / 2
                for i in range(n_tws)]

    smooth = p96.copy()
    is_measured = ~np.isnan(p96)

    # --- Pass 1: Set TWA 0-10° to near-zero for all TWS ---
    # Boats barely move when heading directly into the wind
    for ti in range(n_tws):
        if np.isnan(smooth[0, ti]):
            # Use 30% of whatever TWA 10-20 or 20-30 gives, or a small value
            for neighbor in [1, 2]:
                if not np.isnan(smooth[neighbor, ti]):
                    smooth[0, ti] = smooth[neighbor, ti] * 0.3
                    break
            if np.isnan(smooth[0, ti]):
                smooth[0, ti] = 0.5  # fallback

    # --- Pass 2: 1D interpolation along TWA for each TWS ---
    for ti in range(n_tws):
        col = smooth[:, ti]
        valid = ~np.isnan(col)
        if valid.sum() < 2:
            continue
        valid_idx = np.where(valid)[0]
        valid_twa = [twa_mids[i] for i in valid_idx]
        valid_stw = [col[i] for i in valid_idx]

        for ai in range(n_twa):
            if not np.isnan(col[ai]):
                continue
            twa = twa_mids[ai]
            # Only interpolate (not extrapolate far)
            if twa < min(valid_twa) - 15 or twa > max(valid_twa) + 15:
                continue
            # Linear interpolation
            stw = np.interp(twa, valid_twa, valid_stw)
            smooth[ai, ti] = stw

    # --- Pass 3: 1D interpolation along TWS for each TWA ---
    for ai in range(n_twa):
        row = smooth[ai, :]
        valid = ~np.isnan(row)
        if valid.sum() < 2:
            continue
        valid_idx = np.where(valid)[0]
        valid_tws = [tws_mids[i] for i in valid_idx]
        valid_stw = [row[i] for i in valid_idx]

        for ti in range(n_tws):
            if not np.isnan(row[ti]):
                continue
            tws = tws_mids[ti]
            if tws < min(valid_tws) - 3 or tws > max(valid_tws) + 3:
                continue
            stw = np.interp(tws, valid_tws, valid_stw)
            smooth[ai, ti] = stw

    # --- Pass 4: Iterative 2D neighbor averaging for any remaining NaN ---
    for iteration in range(5):
        still_nan = np.isnan(smooth)
        if not still_nan.any():
            break
        new_smooth = smooth.copy()
        for ai in range(n_twa):
            for ti in range(n_tws):
                if not np.isnan(smooth[ai, ti]):
                    continue
                neighbors = []
                for da, dt in [(-1, 0), (1, 0), (0, -1), (0, 1),
                               (-1, -1), (-1, 1), (1, -1), (1, 1)]:
                    na, nt = ai + da, ti + dt
                    if 0 <= na < n_twa and 0 <= nt < n_tws:
                        if not np.isnan(smooth[na, nt]):
                            neighbors.append(smooth[na, nt])
                if len(neighbors) >= 2:
                    new_smooth[ai, ti] = np.mean(neighbors)
        smooth = new_smooth

    # --- Pass 5: Apply physical smoothing constraints ---
    # For each TWS, ensure STW doesn't decrease too sharply between adjacent TWA bins
    # (small decreases are fine, big jumps are suspect)
    for ti in range(n_tws):
        col = smooth[:, ti]
        if np.isnan(col).all():
            continue
        # Light Gaussian-like smoothing: weighted average with neighbors
        smoothed_col = col.copy()
        for ai in range(1, n_twa - 1):
            if np.isnan(col[ai - 1]) or np.isnan(col[ai]) or np.isnan(col[ai + 1]):
                continue
            # Only smooth cells that weren't well-measured
            if is_measured[ai, ti] and minutes[ai, ti] >= 3.0:
                continue
            smoothed_col[ai] = 0.25 * col[ai - 1] + 0.5 * col[ai] + 0.25 * col[ai + 1]
        smooth[:, ti] = smoothed_col

    # --- Pass 6: Ensure STW increases with TWS (mostly) ---
    # For each TWA, if STW at higher TWS is lower than lower TWS, nudge it up
    for ai in range(n_twa):
        for ti in range(1, n_tws):
            if np.isnan(smooth[ai, ti]) or np.isnan(smooth[ai, ti - 1]):
                continue
            # Allow small decreases in very high wind (overpowered), but not big ones
            if smooth[ai, ti] < smooth[ai, ti - 1] * 0.85:
                smooth[ai, ti] = smooth[ai, ti - 1] * 0.95

    # Round to 1 decimal
    smooth = np.round(smooth, 1)

    return smooth

def build_output(data, smooth, p96, minutes):
    """Build the output JSON structure."""
    n_twa = len(data["twa_labels"])
    n_tws = len(data["tws_labels"])
    twa_mids = [(data["twa_edges"][i] + data["twa_edges"][i + 1]) / 2
                for i in range(n_twa)]
    tws_mids = [(data["tws_edges"][i] + data["tws_edges"][i + 1]) / 2
                for i in range(n_tws)]

    curves = []
    for ti in range(n_tws):
        points = []
        for ai in range(n_twa):
            if np.isnan(smooth[ai, ti]):
                continue
            points.append({
                "twa": twa_mids[ai],
                "stw": float(smooth[ai, ti]),
                "measured": bool(~np.isnan(p96[ai, ti])),
                "minutes": float(minutes[ai, ti]),
            })
        curves.append({
            "tws_label": data["tws_labels"][ti],
            "tws_mid": tws_mids[ti],
            "tws_range": [data["tws_edges"][ti], data["tws_edges"][ti + 1]],
            "points": points,
        })

    # Also build a flat lookup table: twa_mid → tws_mid → stw
    table = {}
    for ai in range(n_twa):
        twa = twa_mids[ai]
        for ti in range(n_tws):
            if np.isnan(smooth[ai, ti]):
                continue
            tws = tws_mids[ti]
            table[f"{twa:.0f}|{tws:.0f}"] = float(smooth[ai, ti])

    return {
        "percentile": PERCENTILE,
        "min_minutes": MIN_MINUTES,
        "twa_mids": [float(t) for t in twa_mids],
        "tws_mids": [float(t) for t in tws_mids],
        "twa_labels": data["twa_labels"],
        "tws_labels": data["tws_labels"],
        "curves": curves,
        "table": table,
    }


def main():
    data = load_grid()
    print(f"Grid: {len(data['twa_labels'])} TWA × {len(data['tws_labels'])} TWS "
          f"× {len(data['stw_labels'])} STW bins")

    p96, minutes = compute_p96_table(data)
    print_coverage(data, p96, minutes)

    print(f"\n--- Interpolating missing values ---")
    smooth = interpolate_polar(data, p96, minutes)

    # Print result
    print(f"\nSmoothed P{PERCENTILE} Polar Table (knots)")
    print(f"{'TWA':<10}", end="")
    for tws in data["tws_labels"]:
        print(f"{tws:>7}", end="")
    print()
    for ai in range(len(data["twa_labels"])):
        print(f"{data['twa_labels'][ai]:<10}", end="")
        for ti in range(len(data["tws_labels"])):
            if np.isnan(smooth[ai, ti]):
                print(f"{'—':>7}", end="")
            else:
                marker = " " if not np.isnan(p96[ai, ti]) else "*"
                print(f"{smooth[ai, ti]:>6.1f}{marker}", end="")
        print()

    print("\n(* = interpolated)")

    # Count coverage
    total = smooth.size
    filled = np.count_nonzero(~np.isnan(smooth))
    measured = np.count_nonzero(~np.isnan(p96))
    interpolated = filled - measured
    print(f"\nMeasured: {measured}, Interpolated: {interpolated}, "
          f"Still missing: {total - filled}, Total: {filled}/{total}")

    output = build_output(data, smooth, p96, minutes)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nWritten to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
