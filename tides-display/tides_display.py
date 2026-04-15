#!/usr/bin/env python3
"""E-paper Dublin dashboard: tides, wind, sea conditions.

Left panel:  Dublin Port tidal heights with curve and phase
Middle panel: Dublin Bay wind — 8h obs vs forecast overlay
Right panel:  Sea conditions — temp, waves, pressure

Data sources:
- Marine Institute Ireland ERDDAP (tides, predictions)
- Irish Lights CIL MetOcean API (Dublin Bay buoy obs)
- GFS via ERDDAP (wind forecast)

Updates on new data (polls every 60s).
"""

import sys
import os
import time
import math
import signal
import logging
from datetime import datetime, timezone, timedelta

import requests
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.environ.get(
    "EPAPER_LIB_PATH",
    os.path.expanduser("~/e-Paper/RaspberryPi_JetsonNano/python/lib")))

from logging.handlers import RotatingFileHandler

log = logging.getLogger("tides_display")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
# Console
_ch = logging.StreamHandler()
_ch.setFormatter(_fmt)
log.addHandler(_ch)
# Rotating file: 1MB max, keep 3 backups
_fh = RotatingFileHandler(
    os.path.expanduser("~/tides_display.log"),
    maxBytes=1_000_000, backupCount=3)
_fh.setFormatter(_fmt)
log.addHandler(_fh)

# --- Config ---
WIDTH, HEIGHT = 800, 480
POLL_INTERVAL = 60
MIN_REFRESH_GAP = 120

ERDDAP = "https://erddap.marine.ie/erddap/tabledap"

# Irish Lights Dublin Bay buoy
CIL_TOKEN = "B9EF21E2-C563-4C07-94E9-198AF132C447"
CIL_MMSI = "992501301"
CIL_BASE = "https://cilpublic.cil.ie/MetOcean/MetOcean.ashx"


# --- Time helpers ---

def to_local(dt_obj):
    """UTC to Irish local (IST/BST)."""
    year = dt_obj.year
    mar31 = datetime(year, 3, 31, 1, 0, tzinfo=timezone.utc)
    bst_start = mar31 - timedelta(days=(mar31.weekday() + 1) % 7)
    oct31 = datetime(year, 10, 31, 1, 0, tzinfo=timezone.utc)
    bst_end = oct31 - timedelta(days=(oct31.weekday() + 1) % 7)
    dt_utc = dt_obj if dt_obj.tzinfo else dt_obj.replace(tzinfo=timezone.utc)
    if bst_start <= dt_utc < bst_end:
        return dt_obj + timedelta(hours=1)
    return dt_obj


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def local_str(dt_obj):
    return to_local(dt_obj).strftime("%H:%M")


# --- API: Tides ---

def fetch_tide_obs(hours=8):
    """Dublin Port recent observations."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    url = (f"{ERDDAP}/IrishNationalTideGaugeNetwork.json"
           f"?time,station_id,Water_Level_LAT,Water_Level_OD_Malin"
           f"&station_id=%22Dublin+Port%22&time%3E={since}"
           f"&orderBy(%22time%22)")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()["table"]
    cols = data["columnNames"]
    return [dict(zip(cols, row)) for row in data["rows"]]


def fetch_tide_pred_curve(hours_back=8, hours_fwd=8):
    """Continuous tide prediction for curve."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(hours=hours_fwd)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{ERDDAP}/imiTidePrediction.json"
           f"?time,stationID,Water_Level"
           f"&stationID=%22Dublin_Port%22"
           f"&time%3E={start}&time%3C={end}"
           f"&orderBy(%22time%22)")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()["table"]
        cols = data["columnNames"]
        return [dict(zip(cols, row)) for row in data["rows"]]
    except Exception as e:
        log.warning("Tide pred curve: %s", e)
        return []


def fetch_tide_highlow(hours_back=12, hours_fwd=12):
    """High/low events in window."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(hours=hours_fwd)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{ERDDAP}/IMI_TidePrediction_HighLow.json"
           f"?time,stationID,Water_Level_ODMalin,tide_time_category"
           f"&stationID=%22Dublin_Port%22"
           f"&time%3E={start}&time%3C={end}"
           f"&orderBy(%22time%22)")
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()["table"]
        cols = data["columnNames"]
        return [dict(zip(cols, row)) for row in data["rows"]]
    except Exception as e:
        log.warning("Tide H/L: %s", e)
        return []


# --- API: Dublin Bay buoy (Irish Lights) ---

def fetch_dublin_bay_obs(hours=8):
    """Hourly observations from Irish Lights Dublin buoy."""
    from_date = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%d/%m/%Y %H:%M:%S")
    url = (f"{CIL_BASE}?accesstoken={CIL_TOKEN}"
           f"&MMSI={CIL_MMSI}&FromDate={from_date}")
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        raw = r.json()
        data = raw.get("MetOceanData", [])
        if not data:
            log.warning("Dublin Bay obs: API returned OK but MetOceanData empty. "
                        "Keys: %s", list(raw.keys()))
        # Sort chronologically (API returns newest first)
        data.sort(key=lambda x: x.get("hour", ""))
        return data
    except Exception as e:
        log.warning("Dublin Bay obs: %s (url=%s)", e, url[:120])
        return []


# --- API: Wind forecast (DMI HARMONIE-AROME via Open-Meteo) ---

# Dublin Bay SWM buoy coordinates
FORECAST_LAT = 53.332
FORECAST_LON = -6.078

def fetch_wind_forecast(hours_back=8, hours_fwd=8):
    """DMI HARMONIE-AROME wind forecast for Dublin Bay via Open-Meteo."""
    params = {
        "latitude": FORECAST_LAT,
        "longitude": FORECAST_LON,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
                  "pressure_msl",
        "models": "dmi_harmonie_arome_europe",
        "wind_speed_unit": "kn",
        "timezone": "UTC",
        "past_hours": hours_back,
        "forecast_hours": hours_fwd,
    }
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params=params, timeout=30)
        r.raise_for_status()
        hourly = r.json()["hourly"]
        times = hourly["time"]
        result = []
        for i, t in enumerate(times):
            result.append({
                "time": t + ":00Z" if not t.endswith("Z") else t,
                "WindSpeed": hourly.get("wind_speed_10m", [None])[i],
                "WindDirection": hourly.get("wind_direction_10m", [None])[i],
                "WindGust": hourly.get("wind_gusts_10m", [None])[i],
                "AtmosphericPressure": hourly.get("pressure_msl", [None])[i],
            })
        return result
    except Exception as e:
        log.warning("DMI HARMONIE forecast: %s", e)
        return []


# --- Data assembly ---

def get_all_data():
    """Fetch all data for the display."""
    tide_obs = fetch_tide_obs(hours=8)
    tide_curve = fetch_tide_pred_curve(hours_back=8, hours_fwd=8)
    tide_hl = fetch_tide_highlow(hours_back=12, hours_fwd=12)
    bay_obs = fetch_dublin_bay_obs(hours=8)
    wind_fc = fetch_wind_forecast(hours_back=8, hours_fwd=8)

    # Diagnostic: log data counts and key fields
    log.info("Data: tide_obs=%d tide_curve=%d tide_hl=%d bay_obs=%d wind_fc=%d",
             len(tide_obs), len(tide_curve), len(tide_hl),
             len(bay_obs), len(wind_fc))
    if bay_obs:
        raw_latest = bay_obs[-1]
        fields = {k: raw_latest.get(k) for k in
                  ["hour", "WaveHeight", "WavePeriod", "WaterTemperature",
                   "AverageWindSpeed", "WindDirection"]}
        all_none = all(v is None for k, v in fields.items() if k != "hour")
        if all_none:
            log.warning("Bay obs: latest record %s has all-None readings, "
                        "falling back", raw_latest.get("hour"))
        else:
            log.info("Bay latest: %s", fields)
    else:
        log.warning("Bay obs: EMPTY — Irish Lights API returned no data")

    # Current tide level
    current = tide_obs[-1] if tide_obs else {}
    obs_time = current.get("time", "")
    level_lat = current.get("Water_Level_LAT")
    level_od = current.get("Water_Level_OD_Malin")

    # Trend
    trend = "—"
    if len(tide_obs) >= 6:
        recent = [r["Water_Level_LAT"] for r in tide_obs[-6:]
                  if r["Water_Level_LAT"] is not None]
        if len(recent) >= 4:
            diff = recent[-1] - recent[0]
            if diff > 0.05:
                trend = "Rising"
            elif diff < -0.05:
                trend = "Falling"
            else:
                trend = "Slack"

    # Phase relative to nearest HW
    now = datetime.now(timezone.utc)
    hw_events = [e for e in tide_hl if "HIGH" in e.get("tide_time_category", "").upper()]
    phase_str = ""
    if hw_events:
        nearest_hw = min(hw_events, key=lambda e: abs((parse_iso(e["time"]) - now).total_seconds()))
        hw_time = parse_iso(nearest_hw["time"])
        delta_h = (now - hw_time).total_seconds() / 3600
        if abs(delta_h) < 0.25:
            phase_str = "HW"
        elif delta_h > 0:
            phase_str = f"HW+{delta_h:.0f}"
        else:
            phase_str = f"HW{delta_h:.0f}"

    # Previous and next H/L events
    prev_events = sorted([e for e in tide_hl if parse_iso(e["time"]) <= now],
                         key=lambda e: e["time"], reverse=True)
    next_events = sorted([e for e in tide_hl if parse_iso(e["time"]) > now],
                         key=lambda e: e["time"])

    # Latest bay obs — skip records with all-None readings
    latest_bay = {}
    for obs in reversed(bay_obs):
        if any(obs.get(k) is not None for k in
               ("AverageWindSpeed", "WaveHeight", "WaterTemperature")):
            latest_bay = obs
            break
    if bay_obs and not latest_bay:
        log.warning("Bay obs: all %d records have None readings", len(bay_obs))

    # Dublin Port: LAT is 2.458m below OD Malin
    LAT_OFFSET = 2.458

    # Convert H/L events from OD Malin to LAT
    for ev in tide_hl:
        od = ev.get("Water_Level_ODMalin")
        if od is not None:
            ev["Water_Level_LAT"] = od + LAT_OFFSET

    # Tidal coefficient / springs-neaps
    # Dublin Port: mean spring range ~3.6m OD Malin, mean neap ~1.9m
    MEAN_SPRING_RANGE = 3.6
    MEAN_NEAP_RANGE = 1.9
    coeff = None
    coeff_label = ""
    sorted_hl = sorted(tide_hl, key=lambda e: e["time"])
    for i in range(len(sorted_hl) - 1):
        e1 = sorted_hl[i]
        e2 = sorted_hl[i + 1]
        t1 = parse_iso(e1["time"])
        t2 = parse_iso(e2["time"])
        # Use the pair that brackets now
        if t1 <= now <= t2:
            l1 = e1.get("Water_Level_ODMalin", 0) or 0
            l2 = e2.get("Water_Level_ODMalin", 0) or 0
            rng = abs(l2 - l1)
            # Coefficient: 20 at neaps, 120 at springs (French system approx)
            coeff = int(20 + (rng - MEAN_NEAP_RANGE) / (MEAN_SPRING_RANGE - MEAN_NEAP_RANGE) * 75)
            coeff = max(20, min(120, coeff))
            if coeff >= 90:
                coeff_label = "Springs"
            elif coeff >= 70:
                coeff_label = "Moderate"
            elif coeff >= 45:
                coeff_label = "Neaps"
            else:
                coeff_label = "Neaps"
            break

    return {
        "tide_obs": tide_obs,
        "tide_curve": tide_curve,
        "tide_hl": tide_hl,
        "level_lat": level_lat,
        "level_od": level_od,
        "obs_time": obs_time,
        "trend": trend,
        "phase": phase_str,
        "prev_hl": prev_events[:2],
        "next_hl": next_events[:2],
        "coeff": coeff,
        "coeff_label": coeff_label,
        "bay_obs": bay_obs,
        "latest_bay": latest_bay,
        "wind_fc": wind_fc,
    }


# --- Drawing helpers ---

def deg_to_compass(deg):
    """Convert degrees to 16-point compass."""
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[round(deg / 22.5) % 16]


def draw_arrow_glyph(draw, x, y, direction, size=14):
    if direction == "Rising":
        draw.polygon([(x, y - size), (x - size // 2, y), (x + size // 2, y)],
                     fill=0)
        draw.rectangle([x - size // 6, y, x + size // 6, y + size // 2], fill=0)
    elif direction == "Falling":
        draw.polygon([(x, y + size), (x - size // 2, y), (x + size // 2, y)],
                     fill=0)
        draw.rectangle([x - size // 6, y - size // 2, x + size // 6, y], fill=0)


def draw_wind_arrow(draw, cx, cy, direction_deg, size=8, speed=None, width=2):
    """Draw a wind barb showing where wind comes FROM.

    Shaft points toward the 'from' direction. Barbs at the upwind tip
    on the left side (NH convention). Speed encoded:
      0-2 kts: circle (calm)
      3-7: shaft + 1 short barb
      8-12: shaft + 1 long barb
      13-17: shaft + 1 long + 1 short
      18-22: shaft + 2 long
      23-27: shaft + 2 long + 1 short
    """
    rad = math.radians(direction_deg)
    # 'from' direction in screen coords (y increases downward)
    dx = math.sin(rad)
    dy = -math.cos(rad)

    # Calm — just a circle with dot
    if speed is not None and round(speed / 5) * 5 < 3:
        r = 3
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=1)
        draw.point((cx, cy), fill=0)
        return

    # Shaft centred on (cx, cy)
    x_tip = cx + dx * size
    y_tip = cy + dy * size
    x_tail = cx - dx * size
    y_tail = cy - dy * size
    draw.line([(int(x_tail), int(y_tail)), (int(x_tip), int(y_tip))], fill=0, width=width)

    # Barb direction — left side, angled ~120° from shaft (obtuse)
    # Perpendicular left: (-dy, dx), mixed with forward along shaft (dx, dy)
    perp_x = -dy
    perp_y = dx
    barb_dx = perp_x * 0.85 + dx * 0.5
    barb_dy = perp_y * 0.85 + dy * 0.5

    # Work out barbs from speed (round to nearest 5 kts)
    if speed is None:
        # No speed info — just one short barb
        barbs = [(1.0, 0.5)]
    else:
        rounded = round(speed / 5) * 5
        if rounded < 3:
            # Calm — circle
            r = max(3, size // 3)
            draw.ellipse([int(x_tail) - r, int(y_tail) - r,
                          int(x_tail) + r, int(y_tail) + r], outline=0, width=1)
            draw.point((int(cx), int(cy)), fill=0)
            return
        full = rounded // 10
        half = (rounded % 10) // 5
        barbs = []
        pos = 1.0
        for _ in range(full):
            barbs.append((pos, 1.0))
            pos -= 0.25
        if half:
            # Lone half-barb sits inward from tip; with full barbs it follows
            if not full:
                pos -= 0.25  # offset inward when it's the only barb
            barbs.append((pos, 0.5))

    short_barb = size * 0.45
    long_barb = size * 0.7

    for pos, length in barbs:
        # pos=1.0 is at the tip, pos goes toward tail
        bx0 = x_tip - dx * size * (1.0 - pos) * 2
        by0 = y_tip - dy * size * (1.0 - pos) * 2
        blen = long_barb if length >= 1.0 else short_barb
        bx1 = bx0 + barb_dx * blen
        by1 = by0 + barb_dy * blen
        draw.line([(int(bx0), int(by0)), (int(bx1), int(by1))], fill=0, width=width)


def interp_level(points, target_ts):
    """Interpolate level at a timestamp from sorted (time, level) points."""
    for i in range(len(points) - 1):
        t0, l0 = points[i]
        t1, l1 = points[i + 1]
        if t0 <= target_ts <= t1:
            if t1 == t0:
                return l0
            frac = (target_ts - t0) / (t1 - t0)
            return l0 + frac * (l1 - l0)
    return None


# --- Tidal curve panel ---

def draw_tide_panel(draw, x, y, w, h, data, fonts):
    """Left panel: Dublin Port tides."""
    f_title, f_big, f_bold, f_med, f_small = fonts

    # Panel header
    draw.rectangle([x, y, x + w - 1, y + 24], fill=0)
    draw.text((x + w // 2, y + 12), "DUBLIN PORT", font=f_title, fill=255, anchor="mm")

    # Phase badge + coefficient
    phase = data.get("phase", "")
    coeff = data.get("coeff")
    coeff_label = data.get("coeff_label", "")

    badge_x = x + 4
    cy = y + 28
    if phase:
        bbox = f_bold.getbbox(phase)
        pw = bbox[2] - bbox[0] + 16
        draw.rectangle([badge_x, cy, badge_x + pw, cy + 22], fill=0)
        draw.text((badge_x + pw // 2, cy + 11), phase,
                  font=f_bold, fill=255, anchor="mm")
        badge_x += pw + 4

    # Coefficient drawn after HW/LW rows (see below)

    # Observation time
    obs_time = data.get("obs_time", "")
    if obs_time:
        dt = parse_iso(obs_time)
        draw.text((x + w - 6, cy + 11), f"@ {local_str(dt)}",
                  font=f_bold, fill=0, anchor="rm")

    # Current level + trend on same line
    level = data.get("level_lat")
    level_str = f"{level:.2f}m" if level is not None else "—"
    trend = data.get("trend", "—")
    level_y = y + 54
    draw.text((x + w // 2, level_y), level_str, font=f_big, fill=0, anchor="mt")

    trend_y = level_y + 42
    if trend in ("Rising", "Falling"):
        draw_arrow_glyph(draw, x + w // 2 - 36, trend_y + 6, trend, size=10)
        draw.text((x + w // 2 - 18, trend_y), trend, font=f_med, fill=0, anchor="lt")
    else:
        draw.text((x + w // 2, trend_y), trend, font=f_med, fill=0, anchor="mt")

    # --- Previous / Next H/L (stacked vertically, not side by side) ---
    hl_y = trend_y + 24
    draw.line([(x + 6, hl_y), (x + w - 6, hl_y)], fill=0, width=1)

    prev_hl = data.get("prev_hl", [])
    next_hl = data.get("next_hl", [])

    def draw_hl_row(ev, row_y, prefix):
        t = parse_iso(ev["time"])
        cat = ev.get("tide_time_category", "")
        label = "HW" if "HIGH" in cat.upper() else "LW"
        level = ev.get("Water_Level_LAT")
        lstr = f"{level:.1f}m" if level is not None else ""
        # prefix (Prev/Next) left, then HW/LW time bold, level right
        draw.text((x + 6, row_y), prefix, font=f_small, fill=0, anchor="lt")
        draw.text((x + 42, row_y), f"{label} {local_str(t)}",
                  font=f_bold, fill=0, anchor="lt")
        draw.text((x + w - 6, row_y), lstr, font=f_med, fill=0, anchor="rt")

    row_y = hl_y + 4
    if prev_hl:
        draw_hl_row(prev_hl[0], row_y, "Prev")
        row_y += 20
    if next_hl:
        draw_hl_row(next_hl[0], row_y, "Next")
        row_y += 20
    if len(next_hl) > 1:
        draw_hl_row(next_hl[1], row_y, "")
        row_y += 20

    # Tidal coefficient: label + visual gauge
    if coeff is not None:
        gy = row_y + 8
        # Label: "Neaps, C54"
        draw.text((x + 6, gy), f"{coeff_label}, C{coeff}",
                  font=f_small, fill=0, anchor="lm")
        # Gauge: horizontal line from C30 to C105
        # with ticks at neaps (45) and springs (90), dot at current
        g_min, g_max = 35, 100
        gx1 = x + w // 2 + 4  # gauge start
        gx2 = x + w - 8       # gauge end
        gw = gx2 - gx1
        g_range = g_max - g_min
        draw.line([(gx1, gy), (gx2, gy)], fill=0, width=1)
        # Neaps tick at C45
        nx = gx1 + int((45 - g_min) / g_range * gw)
        draw.line([(nx, gy - 4), (nx, gy + 4)], fill=0, width=1)
        # Springs tick at C90
        sx = gx1 + int((90 - g_min) / g_range * gw)
        draw.line([(sx, gy - 4), (sx, gy + 4)], fill=0, width=1)
        # Current position dot
        cx = gx1 + int((max(g_min, min(g_max, coeff)) - g_min) / g_range * gw)
        draw.ellipse([cx - 3, gy - 3, cx + 3, gy + 3], fill=0)
        row_y += 16

    # --- Tidal curve ---
    curve_y = row_y + 4
    curve_h = h - (curve_y - y) - 6
    curve_x = x + 6
    curve_w = w - 12

    curve_data = data.get("tide_curve", [])
    hl_events = data.get("tide_hl", [])

    if not curve_data or curve_h < 30:
        return

    draw.rectangle([curve_x, curve_y, curve_x + curve_w, curve_y + curve_h],
                   outline=0, width=1)

    now = datetime.now(timezone.utc)

    # Use full curve range
    points = [(parse_iso(p["time"]), p["Water_Level"])
              for p in curve_data if p["Water_Level"] is not None]
    if len(points) < 3:
        return

    t0 = points[0][0].timestamp()
    t1 = points[-1][0].timestamp()
    t_range = t1 - t0 if t1 != t0 else 1
    levels = [p[1] for p in points]
    l_min, l_max = min(levels), max(levels)
    l_range = l_max - l_min if l_max != l_min else 1

    mx, my = 4, 14  # margins (top margin bigger for labels)

    def to_px(ts, lev):
        px = curve_x + mx + (ts - t0) / t_range * (curve_w - 2 * mx)
        py = curve_y + curve_h - my - (lev - l_min) / l_range * (curve_h - 2 * my)
        return (int(px), int(py))

    # Draw curve
    coords = [to_px(p[0].timestamp(), p[1]) for p in points]
    draw.line(coords, fill=0, width=2)

    # "Now" line (vertical dashed)
    now_ts = now.timestamp()
    if t0 <= now_ts <= t1:
        nx = to_px(now_ts, l_min)[0]
        for yy in range(curve_y + 2, curve_y + curve_h - 2, 4):
            draw.line([(nx, yy), (nx, min(yy + 2, curve_y + curve_h - 2))],
                      fill=0, width=1)
        # Bullseye at current level
        now_level = interp_level(
            [(p[0].timestamp(), p[1]) for p in points], now_ts)
        if now_level is not None:
            npx, npy = to_px(now_ts, now_level)
            r = 6
            draw.ellipse([npx - r, npy - r, npx + r, npy + r], fill=0)
            draw.ellipse([npx - 3, npy - 3, npx + 3, npy + 3], fill=255)
            draw.ellipse([npx - 1, npy - 1, npx + 1, npy + 1], fill=0)

    # H/L labels on curve — clamped inside border with padding
    # Measure label height once using font metrics
    sample_bbox = f_small.getbbox("HW 00:00")
    lbl_ascent = -sample_bbox[1]  # pixels above baseline
    lbl_descent = sample_bbox[3]  # pixels below baseline
    lbl_full_h = lbl_ascent + lbl_descent

    for ev in hl_events:
        ev_time = parse_iso(ev["time"])
        ev_ts = ev_time.timestamp()
        if t0 <= ev_ts <= t1:
            ev_level = interp_level(
                [(p[0].timestamp(), p[1]) for p in points], ev_ts)
            if ev_level is not None:
                ex, ey = to_px(ev_ts, ev_level)
                cat = ev.get("tide_time_category", "")
                label = "HW" if "HIGH" in cat.upper() else "LW"
                tstr = local_str(ev_time)
                tag = f"{label} {tstr}"
                tw = f_small.getlength(tag)
                # Clamp x: centre on point but keep inside border
                lx = int(max(curve_x + 3, min(ex - tw / 2,
                             curve_x + curve_w - tw - 3)))
                if label == "HW":
                    # Place above the peak, clamped inside top border
                    ly = max(curve_y + 3, ey - 6 - lbl_full_h)
                    draw.text((lx, ly), tag, font=f_small, fill=0, anchor="lt")
                else:
                    # Place below the trough, clamped inside bottom border
                    ly = min(curve_y + curve_h - 3 - lbl_full_h, ey + 6)
                    draw.text((lx, ly), tag, font=f_small, fill=0, anchor="lt")


# --- Wind panel ---

def draw_wind_panel(draw, x, y, w, h, data, fonts):
    """Middle panel: Dublin Bay wind obs + GFS forecast + direction."""
    f_title, f_big, f_bold, f_med, f_small = fonts

    # Header
    draw.rectangle([x, y, x + w - 1, y + 24], fill=0)
    draw.text((x + w // 2, y + 12), "DUBLIN BAY WIND", font=f_title, fill=255, anchor="mm")

    bay = data.get("latest_bay", {})
    bay_obs = data.get("bay_obs", [])
    wind_fc = data.get("wind_fc", [])

    # Current conditions
    tws = bay.get("AverageWindSpeed")
    gust = bay.get("GustSpeed")
    twd = bay.get("WindDirection")
    bay_time = bay.get("hour", "")

    cy = y + 28
    if bay_time:
        dt = parse_iso(bay_time)
        draw.text((x + 6, cy), f"@ {local_str(dt)}",
                  font=f_bold, fill=0, anchor="lt")

    # Row 1: wind speed (left), barb (centre), direction (right)
    row1_y = cy + 18
    if tws is not None:
        draw.text((x + 6, row1_y), f"{tws} kts",
                  font=f_big, fill=0, anchor="lt")
    if twd is not None:
        compass = deg_to_compass(twd)
        draw.text((x + w - 6, row1_y), f"{twd}°",
                  font=f_big, fill=0, anchor="rt")
        draw.text((x + w - 6, cy), compass,
                  font=f_bold, fill=0, anchor="rt")
        # Bold barb between speed and direction
        draw_wind_arrow(draw, x + w // 2 + 10, row1_y + 18, twd, size=20,
                        speed=tws)

    # Row 2: gust — tighter gap
    row2_y = row1_y + 38
    if gust is not None:
        draw.text((x + 6, row2_y), f"gust {gust} kts",
                  font=f_med, fill=0, anchor="lt")

    # --- Wind chart: obs + forecast ---
    # Barbs go BELOW the chart. Layout: chart, then x-axis labels,
    # then forecast barbs (full row), then obs barbs (half) + key (half)
    barb_row_h = 28  # barbs are size=13, so 26px diameter + padding
    barb_spacing = 6
    label_h = 14  # x-axis time labels below chart
    below_chart = label_h + barb_row_h * 2 + barb_spacing * 3  # labels + 2 barb rows
    chart_y = y + 104  # tight below gust text
    chart_h = (y + h) - below_chart - chart_y
    chart_x = x + 6
    chart_w = w - 12

    if chart_h < 30:
        return

    draw.rectangle([chart_x, chart_y, chart_x + chart_w, chart_y + chart_h],
                   outline=0, width=1)

    now = datetime.now(timezone.utc)

    # Build obs points (speed + direction)
    obs_pts = []
    obs_dir = []
    for o in bay_obs:
        t = parse_iso(o["hour"])
        spd = o.get("AverageWindSpeed")
        d = o.get("WindDirection")
        if spd is not None:
            obs_pts.append((t.timestamp(), spd))
        if d is not None:
            obs_dir.append((t.timestamp(), d, spd))

    # Build forecast points (speed + direction + gusts)
    fc_pts = []
    fc_dir = []
    fc_gust_pts = []
    for f in wind_fc:
        t = parse_iso(f["time"])
        spd = f.get("WindSpeed")
        d = f.get("WindDirection")
        g = f.get("WindGust")
        if spd is not None:
            fc_pts.append((t.timestamp(), spd))
        if d is not None:
            fc_dir.append((t.timestamp(), d, spd))
        if g is not None:
            fc_gust_pts.append((t.timestamp(), g))

    all_pts = obs_pts + fc_pts
    if not all_pts:
        return

    # Centre chart on latest obs time, not clock time
    latest_obs_ts = obs_pts[-1][0] if obs_pts else now.timestamp()
    t_min = latest_obs_ts - 8 * 3600
    t_max = latest_obs_ts + 8 * 3600
    t_range = t_max - t_min

    # Speed range — fit to data with rounded min/max
    all_speeds = [p[1] for p in all_pts if t_min <= p[0] <= t_max]
    gust_pts = []
    for o in bay_obs:
        t = parse_iso(o["hour"])
        g = o.get("GustSpeed")
        if g is not None:
            gust_pts.append((t.timestamp(), g))
            all_speeds.append(g)
    for t, g in fc_gust_pts:
        if t_min <= t <= t_max:
            all_speeds.append(g)

    if not all_speeds:
        return
    s_min = (min(all_speeds) // 5) * 5
    s_max = ((max(all_speeds) // 5) + 1) * 5
    s_max = max(s_max, s_min + 10)  # at least 10 kts range
    s_range = s_max - s_min

    mx, my = 4, 8

    def to_px(ts, spd):
        px = chart_x + mx + (ts - t_min) / t_range * (chart_w - 2 * mx)
        py = chart_y + chart_h - 4 - ((spd - s_min) / s_range) * (chart_h - my - 4)
        return (int(px), int(py))

    # Draw forecast line (thin)
    fc_filtered = [(t, s) for t, s in fc_pts if t_min <= t <= t_max]
    if len(fc_filtered) >= 2:
        fc_coords = [to_px(t, s) for t, s in fc_filtered]
        draw.line(fc_coords, fill=0, width=1)

    # Draw obs line (solid, thick)
    obs_filtered = [(t, s) for t, s in obs_pts if t_min <= t <= t_max]
    if len(obs_filtered) >= 2:
        obs_coords = [to_px(t, s) for t, s in obs_filtered]
        draw.line(obs_coords, fill=0, width=3)

    # Draw obs gust dots (filled)
    for t, g in gust_pts:
        if t_min <= t <= t_max:
            gx, gy = to_px(t, g)
            draw.ellipse([gx - 2, gy - 2, gx + 2, gy + 2], fill=0)

    # Draw forecast gust dots (hollow)
    for t, g in fc_gust_pts:
        if t_min <= t <= t_max:
            gx, gy = to_px(t, g)
            draw.ellipse([gx - 2, gy - 2, gx + 2, gy + 2], outline=0, width=1)

    # Dashed line at latest obs time (data reference)
    obs_nx = to_px(latest_obs_ts, 0)[0]
    for yy in range(chart_y + 2, chart_y + chart_h - 2, 4):
        draw.line([(obs_nx, yy), (obs_nx, min(yy + 2, chart_y + chart_h - 2))],
                  fill=0, width=1)
    # Label for obs reference line — right-aligned
    obs_time_label = local_str(datetime.fromtimestamp(latest_obs_ts, tz=timezone.utc))
    draw.text((obs_nx - 2, chart_y + 3), obs_time_label,
              font=f_small, fill=0, anchor="rt")

    # Clock time dashed line (if different from obs time)
    now_ts = now.timestamp()
    if abs(now_ts - latest_obs_ts) > 300:  # >5 min difference
        cnx = to_px(now_ts, 0)[0]
        for yy in range(chart_y + 4, chart_y + chart_h - 2, 6):
            draw.line([(cnx, yy), (cnx, min(yy + 2, chart_y + chart_h - 2))],
                      fill=0, width=1)
        # Clock time label, left-aligned to the line
        draw.text((cnx + 2, chart_y + 3), local_str(now),
                  font=f_small, fill=0, anchor="lt")

    # Y-axis scale — labels at 5 kts intervals within data range
    for spd_val in range(int(s_min), int(s_max) + 1, 5):
        if s_min <= spd_val <= s_max:
            _, spy = to_px(t_min, spd_val)
            if chart_y + my < spy < chart_y + chart_h - 8:
                draw.text((chart_x + chart_w - 2, spy - 1), str(spd_val),
                          font=f_small, fill=0, anchor="rm")
                draw.line([(chart_x, spy), (chart_x + chart_w - 18, spy)],
                          fill=0, width=1)

    # X-axis time labels — at even hours (matching barb positions)
    label_y = chart_y + chart_h + 2
    obs_centre = datetime.fromtimestamp(latest_obs_ts, tz=timezone.utc)
    for h_off in range(-8, 9, 2):
        t = obs_centre + timedelta(hours=h_off)
        ts = t.timestamp()
        if t_min <= ts <= t_max:
            px = to_px(ts, 0)[0]
            draw.text((px, label_y), to_local(t).strftime("%Hh"),
                      font=f_small, fill=0, anchor="mt")

    # --- Wind barbs below chart ---
    # Row 1: forecast (full width)
    # Row 2: obs (left half) + key (right half)
    barb_base = chart_y + chart_h + barb_spacing + 12  # after x-axis labels
    fc_row_y = barb_base
    obs_row_y = fc_row_y + barb_row_h + barb_spacing

    # Forecast barbs — hourly
    for ts, d, spd in fc_dir:
        if t_min <= ts <= t_max:
            ax = to_px(ts, 0)[0]
            draw_wind_arrow(draw, ax, fc_row_y + barb_row_h // 2, d,
                            size=13, speed=spd, width=1)

    # Obs barbs — hourly
    for ts, d, spd in obs_dir:
        if t_min <= ts <= t_max:
            ax = to_px(ts, 0)[0]
            draw_wind_arrow(draw, ax, obs_row_y + barb_row_h // 2, d,
                            size=13, speed=spd)

    # Key — left-aligned at +1h (between even-hour barbs)
    key_x = to_px(latest_obs_ts + 3600, 0)[0]
    key_y = obs_row_y + barb_row_h + 4
    # thick line + "obs"
    draw.line([(key_x, key_y), (key_x + 18, key_y)], fill=0, width=3)
    draw.text((key_x + 22, key_y), "obs", font=f_small, fill=0, anchor="lm")
    # thin line + "forecast"
    kx2 = key_x + 52
    draw.line([(kx2, key_y), (kx2 + 18, key_y)], fill=0, width=1)
    draw.text((kx2 + 22, key_y), "forecast", font=f_small, fill=0, anchor="lm")


# --- Sea conditions panel ---

def draw_sea_panel(draw, x, y, w, h, data, fonts):
    """Right panel: sea temp, waves, pressure."""
    f_title, f_big, f_bold, f_med, f_small = fonts

    # Header
    draw.rectangle([x, y, x + w - 1, y + 24], fill=0)
    draw.text((x + w // 2, y + 12), "SEA CONDITIONS",
              font=f_title, fill=255, anchor="mm")

    bay = data.get("latest_bay", {})
    bay_time = bay.get("hour", "")

    cy = y + 30
    if bay_time:
        dt = parse_iso(bay_time)
        draw.text((x + w // 2, cy), f"@ {local_str(dt)}",
                  font=f_bold, fill=0, anchor="mt")
        cy += 20

    # Waves (prominent)
    wave_h = bay.get("WaveHeight")
    wave_p = bay.get("WavePeriod")
    if wave_h is not None:
        draw.text((x + w // 2, cy), "Waves", font=f_med, fill=0, anchor="mt")
        wave_str = f"{wave_h:.1f}m"
        if wave_p is not None:
            wave_str += f"  {wave_p}s"
        draw.text((x + w // 2, cy + 16), wave_str,
                  font=f_big, fill=0, anchor="mt")
        cy += 58

    # Separator
    draw.line([(x + 10, cy), (x + w - 10, cy)], fill=0, width=1)
    cy += 6

    # Sea temperature
    sea_temp = bay.get("WaterTemperature")
    if sea_temp is not None:
        draw.text((x + w // 2, cy), "Sea Temp", font=f_med, fill=0, anchor="mt")
        draw.text((x + w // 2, cy + 16), f"{sea_temp:.1f}°C",
                  font=f_bold, fill=0, anchor="mt")
        cy += 42

    # Separator
    draw.line([(x + 10, cy), (x + w - 10, cy)], fill=0, width=1)
    cy += 6

    # Pressure from GFS
    wind_fc = data.get("wind_fc", [])
    now = datetime.now(timezone.utc)
    pressure = None
    for fc in wind_fc:
        fc_time = parse_iso(fc["time"])
        if abs((fc_time - now).total_seconds()) < 3600:
            pressure = fc.get("AtmosphericPressure")
            break
    if pressure is not None:
        draw.text((x + w // 2, cy), "Pressure", font=f_med, fill=0, anchor="mt")
        draw.text((x + w // 2, cy + 16), f"{pressure:.0f} mBar",
                  font=f_bold, fill=0, anchor="mt")
        cy += 42

    # Separator
    draw.line([(x + 10, cy), (x + w - 10, cy)], fill=0, width=1)
    cy += 6

    # Pressure trend (GFS forecast, fixed -8h to +8h window)
    wind_fc = data.get("wind_fc", [])
    pressures = [(parse_iso(f["time"]), f["AtmosphericPressure"])
                 for f in wind_fc if f.get("AtmosphericPressure") is not None]

    remaining = h - (cy - y) - 6
    if len(pressures) >= 3 and remaining > 40:
        draw.text((x + 6, cy), "Pressure - DMI HARMONIE",
                  font=f_small, fill=0, anchor="lt")
        chart_y = cy + 14
        chart_h = remaining - 18
        chart_x = x + 8
        chart_w = w - 16

        draw.rectangle([chart_x, chart_y, chart_x + chart_w,
                        chart_y + chart_h], outline=0, width=1)

        now = datetime.now(timezone.utc)
        t0 = (now - timedelta(hours=8)).timestamp()
        t1 = (now + timedelta(hours=8)).timestamp()
        t_range = t1 - t0

        # Filter pressures to window
        p_in_window = [(t, p) for t, p in pressures
                       if t0 <= t.timestamp() <= t1]
        if not p_in_window:
            return
        p_vals = [p for _, p in p_in_window]

        # Y scale: round down/up to nearest 5 mBar
        p_min = (min(p_vals) // 5) * 5
        p_max = ((max(p_vals) // 5) + 1) * 5
        p_range = p_max - p_min if p_max != p_min else 1

        def p_to_px(ts, pres):
            px = chart_x + 4 + (ts - t0) / t_range * (chart_w - 8)
            py = chart_y + chart_h - 4 - (pres - p_min) / p_range * (chart_h - 12)
            return (int(px), int(py))

        # Fill area between line and 1000 mBar baseline with hatching
        baseline = max(p_min, 1000)
        coords = [p_to_px(t.timestamp(), p) for t, p in p_in_window]

        # Build polygon: line coords + baseline return path
        if baseline <= p_max:
            base_y = p_to_px(t0, baseline)[1]
            poly = list(coords)
            poly.append((coords[-1][0], base_y))
            poly.append((coords[0][0], base_y))
            # Hatching: draw diagonal lines clipped to polygon area
            min_px_x = min(pt[0] for pt in poly)
            max_px_x = max(pt[0] for pt in poly)
            min_px_y = min(pt[1] for pt in poly)
            max_px_y = max(pt[1] for pt in poly)
            # Create a mask for the polygon and draw hatching
            from PIL import Image as PILImage
            mask = PILImage.new("1", (chart_w + 16, chart_h + 16), 0)
            mask_draw = ImageDraw.Draw(mask)
            offset_poly = [(px - chart_x, py - chart_y) for px, py in poly]
            mask_draw.polygon(offset_poly, fill=1)
            # Draw 45-degree hatch lines
            for i in range(-(max_px_y - min_px_y), max_px_x - min_px_x + chart_h, 4):
                lx0 = min_px_x + i - chart_x
                ly0 = min_px_y - chart_y
                lx1 = lx0 + (max_px_y - min_px_y)
                ly1 = max_px_y - chart_y
                # Clip to mask
                for step_y in range(ly0, ly1 + 1):
                    step_x = lx0 + (step_y - ly0)
                    if (0 <= step_x < mask.width and 0 <= step_y < mask.height
                            and mask.getpixel((step_x, step_y))):
                        draw.point((step_x + chart_x, step_y + chart_y), fill=0)

        # Draw the pressure line on top
        draw.line(coords, fill=0, width=2)

        # 1000 mBar reference line (dashed)
        if p_min <= 1000 <= p_max:
            ref_y = p_to_px(t0, 1000)[1]
            for lx in range(chart_x + 2, chart_x + chart_w - 2, 6):
                draw.line([(lx, ref_y), (min(lx + 3, chart_x + chart_w - 2), ref_y)],
                          fill=0, width=1)

        # Now dashed line
        now_ts = now.timestamp()
        nx = p_to_px(now_ts, p_min)[0]
        for yy in range(chart_y + 2, chart_y + chart_h - 2, 4):
            draw.line([(nx, yy), (nx, min(yy + 2, chart_y + chart_h - 2))],
                      fill=0, width=1)

        # Time labels: -8h, now, +8h
        label_y = chart_y + chart_h + 3
        draw.text((chart_x + 4, label_y), "-8h",
                  font=f_small, fill=0, anchor="mt")
        draw.text((nx, label_y), local_str(now),
                  font=f_small, fill=0, anchor="mt")
        draw.text((chart_x + chart_w - 4, label_y), "+8h",
                  font=f_small, fill=0, anchor="mt")

        # Y labels at rounded intervals — skip if too close to edges
        for p_label in range(int(p_min), int(p_max) + 1, 5):
            if p_min <= p_label <= p_max:
                ly = p_to_px(t0, p_label)[1]
                if chart_y + 8 < ly < chart_y + chart_h - 8:
                    draw.text((chart_x + 2, ly),
                              f"{p_label}", font=f_small, fill=0, anchor="lm")


# --- Main render ---

def render_display(data):
    img = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(img)

    try:
        f_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        f_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        f_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        f_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        f_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except OSError:
        f_title = f_big = f_bold = f_med = f_small = ImageFont.load_default()

    fonts = (f_title, f_big, f_bold, f_med, f_small)

    # Top bar
    draw.rectangle([0, 0, WIDTH, 22], fill=0)
    now_local = to_local(datetime.now(timezone.utc))
    draw.text((6, 11), "DUBLIN", font=f_title, fill=255, anchor="lm")
    draw.text((WIDTH // 2, 11), "Courtesy of Digimill  |  Try racemarks.app for more sailing tools",
              font=f_small, fill=255, anchor="mm")
    draw.text((WIDTH - 6, 11), now_local.strftime("%H:%M IST  %d %b %Y"),
              font=f_small, fill=255, anchor="rm")

    # Three panels
    panel_y = 24
    panel_h = HEIGHT - panel_y - 16

    col1_w = 200  # tides (25%)
    col2_w = 400  # wind (50%)
    col3_w = WIDTH - col1_w - col2_w  # sea conditions (25%)

    draw_tide_panel(draw, 0, panel_y, col1_w, panel_h, data, fonts)
    draw.line([(col1_w, panel_y), (col1_w, HEIGHT - 16)], fill=0, width=1)
    draw_wind_panel(draw, col1_w, panel_y, col2_w, panel_h, data, fonts)
    draw.line([(col1_w + col2_w, panel_y), (col1_w + col2_w, HEIGHT - 16)],
              fill=0, width=1)
    draw_sea_panel(draw, col1_w + col2_w, panel_y, col3_w, panel_h, data, fonts)

    # Footer — aligned under panels
    draw.line([(0, HEIGHT - 14), (WIDTH, HEIGHT - 14)], fill=0, width=1)
    fy = HEIGHT - 6
    draw.text((col1_w // 2, fy), "LAT datum  |  MI Ireland",
              font=f_small, fill=0, anchor="mm")
    draw.text((col1_w + col2_w // 2, fy), "Irish Lights  |  DMI HARMONIE",
              font=f_small, fill=0, anchor="mm")
    draw.text((col1_w + col2_w + col3_w // 2, fy), "Irish Lights  |  DMI HARMONIE",
              font=f_small, fill=0, anchor="mm")

    return img


# --- E-paper ---

def init_epd():
    from waveshare_epd import epd7in5_V2
    epd = epd7in5_V2.EPD()
    epd.init()
    return epd


def display_image(epd, img):
    img = img.rotate(180)
    epd.display(epd.getbuffer(img))


def cleanup_epd(epd):
    try:
        epd.sleep()
    except Exception:
        pass


# --- Main loop ---

_running = True


def _signal_handler(sig, frame):
    global _running
    log.info("Signal %s received, shutting down...", sig)
    _running = False


def main():
    global _running
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    preview_mode = "--preview" in sys.argv

    epd = None
    if not preview_mode:
        log.info("Initializing e-paper display...")
        epd = init_epd()

    last_obs_key = None
    last_refresh = 0

    try:
        while _running:
            log.info("Polling data...")
            try:
                data = get_all_data()
                obs_key = (data.get("obs_time", ""),
                           data.get("latest_bay", {}).get("hour", ""))
                has_new = obs_key != last_obs_key

                log.info("Tide: %.2fm %s %s | Bay: %s kts @ %s° | %s",
                         data.get("level_lat") or 0,
                         data.get("trend", "?"),
                         data.get("phase", ""),
                         data.get("latest_bay", {}).get("AverageWindSpeed", "?"),
                         data.get("latest_bay", {}).get("WindDirection", "?"),
                         "NEW" if has_new else "no change")

            except Exception as e:
                log.error("Data fetch error: %s", e)
                data = None
                has_new = False

            if preview_mode and data:
                img = render_display(data)
                img.save("tides_preview.png")
                log.info("Preview saved")
                break

            now_ts = time.time()
            if data and has_new and (now_ts - last_refresh) >= MIN_REFRESH_GAP:
                log.info("Refreshing display...")
                img = render_display(data)
                display_image(epd, img)
                last_obs_key = obs_key
                last_refresh = time.time()
                log.info("Display updated.")
            elif not has_new:
                log.info("No new data.")

            for _ in range(POLL_INTERVAL // 5):
                if not _running:
                    break
                time.sleep(5)
    finally:
        if epd:
            log.info("Putting display to sleep...")
            cleanup_epd(epd)
        log.info("Done.")


if __name__ == "__main__":
    main()
