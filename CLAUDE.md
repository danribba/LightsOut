# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LightsOut Hue Analyzer is a Python headless service that monitors Philips Hue lights, logs state changes, and detects usage patterns to predict and automate lighting behavior.

## Commands

```bash
# Setup
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt

# Run service (continuous monitoring)
python main.py

# Run with custom config
python main.py -c /path/to/config.yaml

# One-time pattern analysis
python main.py --analyze

# Show status
python main.py --status
```

## Architecture

The service follows a pipeline architecture:

```
HueBridge → EventLogger → Database → PatternDetector → LightingPredictor
    ↑                                                         ↓
    └──────────────── Automation (optional) ──────────────────┘
```

**Core components:**

- `src/service.py` - Main orchestrator (`HueAnalyzerService`). Initializes all components, runs APScheduler jobs for polling (every N seconds), daily analysis (3 AM), and weekly cleanup (Sunday 4 AM).

- `src/hue/bridge.py` - Hue Bridge communication via `phue` library. Handles connection, auto-discovery, light state polling, and change detection. Stores previous states to detect changes.

- `src/storage/database.py` - SQLAlchemy/SQLite storage with three tables: `light_events` (state changes with time context), `light_snapshots` (periodic state captures), `detected_patterns` (patterns with confidence scores).

- `src/analyzer/pattern_detector.py` - Analyzes events using pandas. Detects three pattern types:
  - Time-based: "Kitchen turns on at 07:00 on weekdays"
  - Sequences: "When hall turns on, kitchen turns on within 2 min"
  - Correlations: "Living room and dining room turn on together"

- `src/analyzer/predictor.py` - Uses detected patterns to predict actions and trigger automations.

- `src/api/server.py` - Flask REST API for remote access. Runs in background thread on port 5000.

## Configuration

All settings in `config.yaml`. Key sections:
- `hue.bridge_ip` - Leave empty for auto-discovery
- `hue.poll_interval` - Seconds between state checks (default: 10)
- `analyzer.confidence_threshold` - Minimum pattern confidence (0.0-1.0)
- `automation.enabled` / `automation.dry_run` - Control automatic actions

## Data Storage

SQLite database at `data/hue_events.db`. Configurable retention (default 90 days). Database can be swapped via SQLAlchemy connection string in `database.py`.

## First Run

On first connection, press the Hue Bridge link button within 30 seconds. Credentials are cached by `phue` library.

## Deployment

### Docker (Recommended for Raspberry Pi)

```bash
# Build and run
docker compose up -d

# View logs
docker compose logs -f

# Rebuild after code changes
docker compose up -d --build
```

Note: Uses `network_mode: host` to access Hue Bridge on local network.

### Systemd Service (Native on Raspberry Pi)

```bash
# Install service
sudo cp lightsout.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable lightsout
sudo systemctl start lightsout

# View logs
journalctl -u lightsout -f

# Restart after config changes
sudo systemctl restart lightsout
```

Adjust `WorkingDirectory` and `User` in `lightsout.service` if not using default pi user.

## Dashboard

Web dashboard available at `http://<pi-ip>:5000/`

Shows: live light status, recent events, detected patterns, activity graph by hour. Auto-refreshes every 30 seconds.

## REST API

API runs on port 5000 by default (configurable in `config.yaml`).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/status` | GET | System status and statistics |
| `/api/lights` | GET | Current state of all lights |
| `/api/rooms` | GET | All rooms/zones |
| `/api/events` | GET | Recent events (query: `limit`, `light_id`, `event_type`, `days`) |
| `/api/events/summary` | GET | Aggregated stats by light and hour |
| `/api/patterns` | GET | Detected patterns |
| `/api/analyze` | POST | Trigger pattern analysis |

Example: `curl http://<pi-ip>:5000/api/patterns`
