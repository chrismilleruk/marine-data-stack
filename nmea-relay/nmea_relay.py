#!/usr/bin/env python3
"""NMEA Relay — watches a directory for NMEA log files and ingests to TimescaleDB.

Designed to run on the receiving end of a Syncthing sync. Watches a directory
for new or updated .nmea/.log files, parses nav data, and inserts to TimescaleDB.
Tracks which files (and how far into each) have been processed via a local
SQLite state database, so it can resume after restarts.

Usage:
    # Watch a syncthing folder for new files
    python3 nmea_relay.py --watch /path/to/syncthing/signalk-logs

    # One-shot import of specific files
    python3 nmea_relay.py --file track1.nmea track2.nmea

    # Import all files in a directory
    python3 nmea_relay.py --import-dir /path/to/tracks
"""

import argparse
import glob
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    psycopg2 = None
    execute_values = None

log = logging.getLogger("nmea_relay")


# ---------------------------------------------------------------------------
# NMEA parsing (from parse_nmea.py)
# ---------------------------------------------------------------------------

def parse_lat_lon(lat_str, ns, lon_str, ew):
    lat_deg = float(lat_str[:2])
    lat_min = float(lat_str[2:])
    lat = lat_deg + (lat_min / 60.0)
    if ns == "S":
        lat = -lat
    lon_deg = float(lon_str[:3])
    lon_min = float(lon_str[3:])
    lon = lon_deg + (lon_min / 60.0)
    if ew == "W":
        lon = -lon
    return lat, lon


def parse_line(line):
    """Parse a single NMEA log line. Returns (timestamp_ms, field_dict) or None."""
    parts = line.strip().split(";")
    if len(parts) != 3 or parts[1] != "N" or not parts[2].startswith("$"):
        return None
    try:
        ts_ms = int(parts[0])
    except ValueError:
        return None

    sentence = parts[2]
    fields = sentence.split(",")

    try:
        if sentence.startswith("$GNRMC") and len(fields) > 9 and fields[2] == "A":
            lat, lon = parse_lat_lon(fields[3], fields[4], fields[5], fields[6])
            return (ts_ms, {"lat": lat, "lon": lon,
                            "sog_knots": float(fields[7]), "cog_deg": float(fields[8])})
        elif sentence.startswith("$IIVHW") and len(fields) >= 6:
            return (ts_ms, {"stw_knots": float(fields[5])})
        elif sentence.startswith("$IIMWV") and len(fields) >= 6 and fields[5].startswith("A"):
            return (ts_ms, {"awa_deg": float(fields[1]), "aws_knots": float(fields[3])})
        elif sentence.startswith("$IIHDG") and len(fields) >= 2:
            return (ts_ms, {"heading_deg": float(fields[1])})
    except (ValueError, IndexError):
        return None
    return None


# ---------------------------------------------------------------------------
# Row aggregator
# ---------------------------------------------------------------------------

NAV_FIELDS = ("lat", "lon", "sog_knots", "cog_deg", "stw_knots",
              "awa_deg", "aws_knots", "heading_deg")


class RowAggregator:
    """Groups NMEA fields arriving within a time window into single rows."""

    def __init__(self, window_ms=1500):
        self.window_ms = window_ms
        self.current_ts = None
        self.current_fields = {}

    def add(self, ts_ms, fields):
        row = None
        if self.current_ts is not None and (ts_ms - self.current_ts) > self.window_ms:
            row = self._emit()
        if self.current_ts is None:
            self.current_ts = ts_ms
        self.current_fields.update(fields)
        return row

    def flush(self):
        if self.current_ts is not None:
            return self._emit()
        return None

    def _emit(self):
        ts = datetime.fromtimestamp(self.current_ts / 1000.0, tz=timezone.utc)
        row = {"time": ts}
        for f in NAV_FIELDS:
            row[f] = self.current_fields.get(f)
        self.current_ts = None
        self.current_fields = {}
        if row.get("lat") is not None:
            return row
        return None


# ---------------------------------------------------------------------------
# File parser
# ---------------------------------------------------------------------------

def parse_file(filepath, start_line=0):
    """Parse an NMEA file from a given line offset. Returns (rows, lines_read)."""
    aggregator = RowAggregator()
    rows = []
    lines_read = 0

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f):
            if i < start_line:
                continue
            lines_read += 1
            result = parse_line(line)
            if result is not None:
                ts_ms, fields = result
                row = aggregator.add(ts_ms, fields)
                if row is not None:
                    rows.append(row)

    row = aggregator.flush()
    if row is not None:
        rows.append(row)

    return rows, start_line + lines_read


# ---------------------------------------------------------------------------
# State tracker (SQLite)
# ---------------------------------------------------------------------------

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_state (
    filepath TEXT PRIMARY KEY,
    lines_processed INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    last_modified REAL,
    updated_at TEXT
);
"""


class StateTracker:
    """Tracks which files have been processed and how far."""

    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(STATE_SCHEMA)

    def get_progress(self, filepath):
        """Returns lines_processed for a file, or 0 if new."""
        cur = self.conn.execute(
            "SELECT lines_processed FROM file_state WHERE filepath = ?",
            (filepath,),
        )
        row = cur.fetchone()
        return row[0] if row else 0

    def update_progress(self, filepath, lines_processed, rows_inserted, mtime):
        self.conn.execute(
            """INSERT INTO file_state (filepath, lines_processed, rows_inserted, last_modified, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(filepath) DO UPDATE SET
                 lines_processed = ?,
                 rows_inserted = rows_inserted + ?,
                 last_modified = ?,
                 updated_at = ?""",
            (filepath, lines_processed, rows_inserted, mtime,
             datetime.now(timezone.utc).isoformat(),
             lines_processed, rows_inserted, mtime,
             datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def needs_processing(self, filepath, current_mtime):
        """Check if file is new or has been modified since last processing."""
        cur = self.conn.execute(
            "SELECT last_modified FROM file_state WHERE filepath = ?",
            (filepath,),
        )
        row = cur.fetchone()
        if row is None:
            return True
        return current_mtime > row[0]

    def summary(self):
        cur = self.conn.execute(
            "SELECT filepath, lines_processed, rows_inserted, updated_at FROM file_state ORDER BY filepath"
        )
        return cur.fetchall()

    def close(self):
        self.conn.close()


# ---------------------------------------------------------------------------
# TimescaleDB writer
# ---------------------------------------------------------------------------

class TimescaleDBWriter:
    def __init__(self, connection_string, device="blacksheep"):
        self.connection_string = connection_string
        self.device = device
        self.conn = None

    def connect(self):
        if self.conn is not None:
            try:
                self.conn.cursor().execute("SELECT 1")
                return True
            except Exception:
                self.conn = None
        try:
            self.conn = psycopg2.connect(self.connection_string, connect_timeout=10)
            self.conn.autocommit = False
            log.info("Connected to TimescaleDB")
            return True
        except Exception as e:
            log.warning("TimescaleDB connection failed: %s", e)
            self.conn = None
            return False

    def insert_rows(self, rows):
        if not rows or not self.connect():
            return 0

        sql = """
            INSERT INTO nav_data (time, device, lat, lon, sog_knots, cog_deg,
                                  stw_knots, awa_deg, aws_knots, heading_deg)
            VALUES %s
            ON CONFLICT DO NOTHING
        """
        values = [
            (row["time"], self.device,
             row.get("lat"), row.get("lon"),
             row.get("sog_knots"), row.get("cog_deg"),
             row.get("stw_knots"), row.get("awa_deg"),
             row.get("aws_knots"), row.get("heading_deg"))
            for row in rows
        ]
        try:
            cur = self.conn.cursor()
            execute_values(cur, sql, values, page_size=1000)
            self.conn.commit()
            return len(rows)
        except Exception as e:
            log.warning("Insert failed: %s", e)
            try:
                self.conn.rollback()
            except Exception:
                pass
            self.conn = None
            return 0

    def close(self):
        if self.conn:
            self.conn.close()


# ---------------------------------------------------------------------------
# Dry-run writer (no DB needed)
# ---------------------------------------------------------------------------

class DryRunWriter:
    """Prints rows instead of inserting. For testing without a database."""

    def __init__(self):
        self.total = 0

    def insert_rows(self, rows):
        self.total += len(rows)
        if rows:
            first = rows[0]
            last = rows[-1]
            log.info("  [dry-run] %d rows: %s .. %s",
                     len(rows), first["time"].isoformat(), last["time"].isoformat())
        return len(rows)

    def close(self):
        log.info("[dry-run] Total: %d rows", self.total)


# ---------------------------------------------------------------------------
# Process a single file
# ---------------------------------------------------------------------------

def process_file(filepath, state, writer, force=False):
    """Parse and ingest a single file. Returns number of new rows inserted."""
    mtime = os.path.getmtime(filepath)

    if not force and not state.needs_processing(filepath, mtime):
        return 0

    start_line = 0 if force else state.get_progress(filepath)
    rows, total_lines = parse_file(filepath, start_line=start_line)

    if not rows:
        state.update_progress(filepath, total_lines, 0, mtime)
        return 0

    inserted = writer.insert_rows(rows)
    if inserted > 0:
        state.update_progress(filepath, total_lines, inserted, mtime)
        log.info("  %s: +%d rows (lines %d-%d)", os.path.basename(filepath),
                 inserted, start_line, total_lines)
    else:
        log.warning("  %s: parsed %d rows but insert failed", os.path.basename(filepath), len(rows))

    return inserted


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def watch_directory(watch_dir, state, writer, poll_interval=30):
    """Watch a directory for new/modified NMEA files and process them."""
    log.info("Watching %s (poll every %ds)", watch_dir, poll_interval)

    while True:
        files = []
        for ext in ("*.nmea", "*.log"):
            files.extend(glob.glob(os.path.join(watch_dir, ext)))
            files.extend(glob.glob(os.path.join(watch_dir, "**", ext), recursive=True))

        total_new = 0
        for filepath in sorted(files):
            total_new += process_file(filepath, state, writer)

        if total_new > 0:
            log.info("Cycle complete: %d new rows", total_new)

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="NMEA Relay — ingest NMEA logs to TimescaleDB")
    parser.add_argument("--file", nargs="+", help="Import specific NMEA files")
    parser.add_argument("--import-dir", help="Import all files in a directory")
    parser.add_argument("--watch", help="Watch directory for new/modified files")
    parser.add_argument("--db-url",
                        default=os.environ.get("TIMESCALE_CONNECTION_STRING", ""),
                        help="TimescaleDB connection string")
    parser.add_argument("--device",
                        default=os.environ.get("DEVICE_NAME", "blacksheep"),
                        help="Device name tag")
    parser.add_argument("--state-db", default="~/.nmea-relay-state.sqlite",
                        help="Path to state tracking database")
    parser.add_argument("--poll-interval", type=int, default=30,
                        help="Seconds between directory polls in watch mode")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and count rows without inserting to DB")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess files even if already processed")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.file and not args.import_dir and not args.watch:
        parser.error("Specify --file, --import-dir, or --watch")

    state_path = os.path.expanduser(args.state_db)
    state = StateTracker(state_path)

    if args.dry_run:
        writer = DryRunWriter()
    else:
        if psycopg2 is None:
            log.error("psycopg2 not installed. Use --dry-run or: pip install psycopg2-binary")
            sys.exit(1)
        if not args.db_url:
            log.error("No --db-url or TIMESCALE_CONNECTION_STRING set. Use --dry-run to test without DB.")
            sys.exit(1)
        writer = TimescaleDBWriter(args.db_url, device=args.device)

    try:
        if args.file:
            for filepath in args.file:
                process_file(filepath, state, writer, force=args.force)
        elif args.import_dir:
            files = []
            for ext in ("*.nmea", "*.log"):
                files.extend(glob.glob(os.path.join(args.import_dir, ext)))
            for filepath in sorted(files):
                process_file(filepath, state, writer, force=args.force)
        elif args.watch:
            watch_directory(args.watch, state, writer,
                            poll_interval=args.poll_interval)

        # Print summary
        summary = state.summary()
        if summary:
            log.info("--- Summary ---")
            total_rows = 0
            for filepath, lines, rows, updated in summary:
                log.info("  %s: %d lines, %d rows", os.path.basename(filepath), lines, rows)
                total_rows += rows
            log.info("  Total: %d rows across %d files", total_rows, len(summary))

    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        writer.close()
        state.close()


if __name__ == "__main__":
    main()
