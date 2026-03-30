#!/usr/bin/env python3
"""NMEA Replay Server — streams recorded tracks over TCP + UDP with HTTP control.

Usage:
    python3 replay_server.py [options]

Options:
    --tracks-dir DIR   Path to tracks/ folder (default: ./tracks)
    --tcp-port PORT    TCP NMEA port (default: 10110)
    --udp-port PORT    UDP NMEA port (default: 10111)
    --udp-dest ADDR    UDP broadcast address (default: 255.255.255.255)
    --http-port PORT   HTTP control API port (default: 8080)
    --track DATE       Auto-load a track on startup (e.g. 2025-07-26)
    --speed X          Initial speed multiplier (default: 1.0)
    --autoplay         Start playback immediately after loading
    --exclude TYPES    Comma-separated sentence types to exclude
    --only TYPES       Comma-separated sentence types to include (exclusive)

HTTP Control API:
    GET  /status          Current state (track, position, speed, playing)
    GET  /tracks          List available tracks from manifest
    POST /load?track=DATE Load a track by date
    POST /play            Start / resume playback
    POST /pause           Pause playback
    POST /speed?x=N       Set speed multiplier
    POST /seek?pct=N      Seek to percentage through track
    POST /seek?utc=ISO    Seek to UTC timestamp
    POST /filter?exclude=A,B  Exclude sentence types
    POST /filter?only=A,B     Include only these sentence types
    POST /filter?clear        Clear all filters
"""

import argparse
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


# ---------------------------------------------------------------------------
# Track loader
# ---------------------------------------------------------------------------

class Track:
    """A loaded NMEA track — parsed into (timestamp_ms, raw_line, sentence_type) tuples."""

    def __init__(self, meta, lines):
        self.meta = meta
        self.lines = lines  # list of (ts_ms, raw_line, sentence_type)
        self.duration_ms = lines[-1][0] - lines[0][0] if lines else 0

    @classmethod
    def load(cls, tracks_dir, date_str, manifest):
        track_meta = None
        for t in manifest.get("tracks", []):
            if t["date"] == date_str:
                track_meta = t
                break
        if not track_meta:
            raise ValueError(f"Track {date_str} not found in manifest")

        filepath = os.path.join(tracks_dir, track_meta["file"])
        lines = []
        with open(filepath, "r", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                parts = raw.split(";", 2)
                if len(parts) < 3:
                    continue
                try:
                    ts_ms = int(parts[0])
                except ValueError:
                    continue
                sentence = parts[2].strip()
                # Extract sentence type
                stype = None
                if sentence and sentence[0] in ("$", "!"):
                    tag = sentence[1:].split(",")[0].split("*")[0]
                    stype = tag
                lines.append((ts_ms, raw, stype))

        return cls(track_meta, lines)


# ---------------------------------------------------------------------------
# Playback engine
# ---------------------------------------------------------------------------

class PlaybackEngine:
    """Manages playback state: position, speed, pause, seeking."""

    def __init__(self):
        self.track = None
        self.position = 0          # index into track.lines
        self.speed = 1.0
        self.playing = False
        self.lock = threading.Lock()

        # Sentence filter
        self.filter_mode = None    # None, "exclude", "only"
        self.filter_types = set()

        # Callbacks
        self.on_sentence = None    # called with (raw_line, sentence_type)

    def load_track(self, track):
        with self.lock:
            self.playing = False
            self.track = track
            self.position = 0

    def play(self):
        with self.lock:
            if not self.track:
                return False
            if not self.playing:
                self.playing = True
                threading.Thread(target=self._playback_loop, daemon=True).start()
            return True

    def pause(self):
        with self.lock:
            self.playing = False

    def set_speed(self, x):
        with self.lock:
            self.speed = max(0.1, min(100.0, x))

    def seek_pct(self, pct):
        with self.lock:
            if not self.track:
                return
            idx = int((pct / 100.0) * len(self.track.lines))
            self.position = max(0, min(idx, len(self.track.lines) - 1))

    def seek_utc(self, utc_str):
        """Seek to a UTC timestamp like '2025-07-26T13:10:00'."""
        with self.lock:
            if not self.track:
                return
            # Parse target
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                        "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(utc_str, fmt)
                    break
                except ValueError:
                    continue
            else:
                return
            target_ms = int(dt.timestamp() * 1000)
            # Binary search
            lo, hi = 0, len(self.track.lines) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if self.track.lines[mid][0] < target_ms:
                    lo = mid + 1
                else:
                    hi = mid
            self.position = lo

    def set_filter(self, mode, types):
        with self.lock:
            self.filter_mode = mode
            self.filter_types = set(types) if types else set()

    def clear_filter(self):
        with self.lock:
            self.filter_mode = None
            self.filter_types = set()

    def get_status(self):
        with self.lock:
            if not self.track:
                return {
                    "loaded": False,
                    "playing": False,
                    "speed": self.speed,
                    "filter_mode": self.filter_mode,
                    "filter_types": sorted(self.filter_types) if self.filter_types else [],
                }
            lines = self.track.lines
            pos = self.position
            pct = (pos / len(lines) * 100) if lines else 0
            current_ts = lines[pos][0] if pos < len(lines) else lines[-1][0]
            current_utc = datetime.utcfromtimestamp(current_ts / 1000).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            return {
                "loaded": True,
                "track": self.track.meta.get("date", ""),
                "playing": self.playing,
                "speed": self.speed,
                "position": pos,
                "total_lines": len(lines),
                "pct": round(pct, 2),
                "current_utc": current_utc,
                "current_ts_ms": current_ts,
                "filter_mode": self.filter_mode,
                "filter_types": sorted(self.filter_types) if self.filter_types else [],
            }

    def _should_send(self, stype):
        if self.filter_mode == "exclude":
            return stype not in self.filter_types
        elif self.filter_mode == "only":
            return stype in self.filter_types
        return True

    def _playback_loop(self):
        while True:
            with self.lock:
                if not self.playing or not self.track:
                    return
                if self.position >= len(self.track.lines):
                    self.playing = False
                    return
                pos = self.position
                speed = self.speed

            ts_ms, raw_line, stype = self.track.lines[pos]

            # Send if passes filter
            if self._should_send(stype) and self.on_sentence:
                self.on_sentence(raw_line, stype)

            # Advance position
            with self.lock:
                self.position = pos + 1
                if self.position >= len(self.track.lines):
                    self.playing = False
                    return
                next_ts = self.track.lines[self.position][0]

            # Sleep for inter-sentence delay
            delta_ms = next_ts - ts_ms
            if delta_ms > 0 and speed > 0:
                sleep_s = (delta_ms / 1000.0) / speed
                # Cap max sleep to 2 seconds (real-time) to avoid huge gaps
                sleep_s = min(sleep_s, 2.0)
                if sleep_s > 0.0001:
                    time.sleep(sleep_s)


# ---------------------------------------------------------------------------
# TCP Server
# ---------------------------------------------------------------------------

class TCPServer:
    """Simple TCP server that accepts multiple clients and broadcasts lines."""

    def __init__(self, port):
        self.port = port
        self.clients = []
        self.lock = threading.Lock()
        self.server_socket = None

    def start(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("0.0.0.0", self.port))
        self.server_socket.listen(5)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            try:
                client, addr = self.server_socket.accept()
                with self.lock:
                    self.clients.append(client)
                print(f"  TCP client connected: {addr}")
            except OSError:
                break

    def send(self, nmea_line):
        """Send an NMEA line to all connected TCP clients."""
        # Extract just the NMEA sentence (after timestamp;source;)
        parts = nmea_line.split(";", 2)
        sentence = parts[2].strip() if len(parts) >= 3 else nmea_line
        data = (sentence + "\r\n").encode("ascii", errors="replace")

        with self.lock:
            dead = []
            for c in self.clients:
                try:
                    c.sendall(data)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    dead.append(c)
            for c in dead:
                self.clients.remove(c)
                try:
                    c.close()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# UDP Broadcaster
# ---------------------------------------------------------------------------

class UDPBroadcaster:
    """Sends NMEA sentences via UDP broadcast."""

    def __init__(self, port, dest="255.255.255.255"):
        self.port = port
        self.dest = dest
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def send(self, nmea_line):
        parts = nmea_line.split(";", 2)
        sentence = parts[2].strip() if len(parts) >= 3 else nmea_line
        data = (sentence + "\r\n").encode("ascii", errors="replace")
        try:
            self.sock.sendto(data, (self.dest, self.port))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# HTTP Control API
# ---------------------------------------------------------------------------

class ControlHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the control API."""

    engine = None       # set by main()
    manifest = None
    tracks_dir = None

    def log_message(self, format, *args):
        # Quieter logging
        print(f"  HTTP: {args[0]}")

    def _json_response(self, data, status=200):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg, status=400):
        self._json_response({"error": msg}, status)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/status":
            self._json_response(self.engine.get_status())
        elif path == "/tracks":
            tracks = []
            for t in self.manifest.get("tracks", []):
                tracks.append({
                    "date": t["date"],
                    "day": t.get("day", ""),
                    "start_area": t.get("start_area", ""),
                    "sentence_count": t.get("sentence_count", 0),
                    "duration_sec": t.get("duration_sec", 0),
                    "race_duration_min": t.get("race_duration_min", 0),
                    "sentences_available": t.get("sentences_available", []),
                })
            self._json_response({"tracks": tracks})
        else:
            self._error("Not found", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path == "/load":
            date = params.get("track", [None])[0]
            if not date:
                return self._error("Missing ?track=DATE parameter")
            try:
                track = Track.load(self.tracks_dir, date, self.manifest)
                self.engine.load_track(track)
                self._json_response({
                    "ok": True,
                    "loaded": date,
                    "lines": len(track.lines),
                    "duration_sec": track.duration_ms / 1000,
                })
            except (ValueError, FileNotFoundError) as e:
                self._error(str(e))

        elif path == "/play":
            if self.engine.play():
                self._json_response({"ok": True, "playing": True})
            else:
                self._error("No track loaded")

        elif path == "/pause":
            self.engine.pause()
            self._json_response({"ok": True, "playing": False})

        elif path == "/speed":
            x = params.get("x", [None])[0]
            if not x:
                return self._error("Missing ?x=N parameter")
            try:
                self.engine.set_speed(float(x))
                self._json_response({"ok": True, "speed": float(x)})
            except ValueError:
                self._error("Invalid speed value")

        elif path == "/seek":
            pct = params.get("pct", [None])[0]
            utc = params.get("utc", [None])[0]
            if pct:
                try:
                    self.engine.seek_pct(float(pct))
                    self._json_response({"ok": True, "seeked_pct": float(pct)})
                except ValueError:
                    self._error("Invalid pct value")
            elif utc:
                self.engine.seek_utc(utc)
                self._json_response({"ok": True, "seeked_utc": utc})
            else:
                self._error("Missing ?pct=N or ?utc=TIMESTAMP")

        elif path == "/filter":
            if "clear" in params:
                self.engine.clear_filter()
                self._json_response({"ok": True, "filter": "cleared"})
            elif "exclude" in params:
                types = params["exclude"][0].split(",")
                self.engine.set_filter("exclude", [t.strip() for t in types])
                self._json_response({"ok": True, "filter": "exclude", "types": types})
            elif "only" in params:
                types = params["only"][0].split(",")
                self.engine.set_filter("only", [t.strip() for t in types])
                self._json_response({"ok": True, "filter": "only", "types": types})
            else:
                self._error("Missing ?exclude=A,B or ?only=A,B or ?clear")

        else:
            self._error("Not found", 404)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="NMEA Replay Server")
    parser.add_argument("--tracks-dir", default="./tracks",
                        help="Path to tracks/ folder")
    parser.add_argument("--tcp-port", type=int, default=10110,
                        help="TCP NMEA port (default: 10110)")
    parser.add_argument("--udp-port", type=int, default=10111,
                        help="UDP NMEA port (default: 10111)")
    parser.add_argument("--udp-dest", default="255.255.255.255",
                        help="UDP broadcast address")
    parser.add_argument("--http-port", type=int, default=8080,
                        help="HTTP control port (default: 8080)")
    parser.add_argument("--track", default=None,
                        help="Auto-load track by date (e.g. 2025-07-26)")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Initial speed multiplier")
    parser.add_argument("--autoplay", action="store_true",
                        help="Start playback immediately")
    parser.add_argument("--exclude", default=None,
                        help="Comma-separated sentence types to exclude")
    parser.add_argument("--only", default=None,
                        help="Comma-separated sentence types to include")
    args = parser.parse_args()

    # Resolve tracks dir
    tracks_dir = os.path.abspath(args.tracks_dir)
    manifest_path = os.path.join(os.path.dirname(tracks_dir), "manifest.json")

    if not os.path.isfile(manifest_path):
        print(f"ERROR: manifest.json not found at {manifest_path}")
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    print(f"NMEA Replay Server")
    print(f"  Tracks:  {tracks_dir} ({manifest.get('track_count', '?')} tracks)")
    print(f"  TCP:     port {args.tcp_port}")
    print(f"  UDP:     port {args.udp_port} → {args.udp_dest}")
    print(f"  HTTP:    port {args.http_port}")
    print()

    # Set up network
    tcp = TCPServer(args.tcp_port)
    tcp.start()

    udp = UDPBroadcaster(args.udp_port, args.udp_dest)

    # Set up playback engine
    engine = PlaybackEngine()
    engine.speed = args.speed

    def on_sentence(raw_line, stype):
        tcp.send(raw_line)
        udp.send(raw_line)

    engine.on_sentence = on_sentence

    # Apply initial filter
    if args.exclude:
        types = [t.strip() for t in args.exclude.split(",")]
        engine.set_filter("exclude", types)
        print(f"  Filter:  exclude {types}")
    elif args.only:
        types = [t.strip() for t in args.only.split(",")]
        engine.set_filter("only", types)
        print(f"  Filter:  only {types}")

    # Auto-load track
    if args.track:
        try:
            track = Track.load(tracks_dir, args.track, manifest)
            engine.load_track(track)
            print(f"  Loaded:  {args.track} ({len(track.lines):,} lines, "
                  f"{track.duration_ms/1000/60:.0f} min)")
            if args.autoplay:
                engine.play()
                print(f"  Playing at {args.speed}x speed")
        except (ValueError, FileNotFoundError) as e:
            print(f"  WARNING: Could not load track {args.track}: {e}")

    # Start HTTP control API
    ControlHandler.engine = engine
    ControlHandler.manifest = manifest
    ControlHandler.tracks_dir = tracks_dir

    httpd = HTTPServer(("0.0.0.0", args.http_port), ControlHandler)
    print(f"\nReady. Control via http://localhost:{args.http_port}/")
    print(f"  GET  /status          — current state")
    print(f"  GET  /tracks          — list available tracks")
    print(f"  POST /load?track=DATE — load a track")
    print(f"  POST /play            — start playback")
    print(f"  POST /pause           — pause")
    print(f"  POST /speed?x=N       — set speed (0.1–100)")
    print(f"  POST /seek?pct=N      — seek to percentage")
    print(f"  POST /seek?utc=TS     — seek to UTC timestamp")
    print(f"  POST /filter?...      — set sentence filter")
    print()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        httpd.shutdown()


if __name__ == "__main__":
    main()
