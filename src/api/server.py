"""REST API server for remote access to LightsOut data."""

import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory, redirect
from flask_cors import CORS
from loguru import logger

from src.hue.bridge import HueBridge
from src.storage.database import Database
from src.analyzer.pattern_detector import PatternDetector

# Static files directory
STATIC_DIR = Path(__file__).parent / "static"


def create_api(
    database: Database,
    bridge: HueBridge,
    pattern_detector: PatternDetector,
) -> Flask:
    """
    Create Flask API application.

    Args:
        database: Database instance
        bridge: HueBridge instance
        pattern_detector: PatternDetector instance

    Returns:
        Configured Flask app.
    """
    app = Flask(__name__, static_folder=str(STATIC_DIR))
    CORS(app)  # Allow cross-origin requests from PC

    @app.route("/")
    def index():
        """Serve dashboard."""
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/dashboard")
    def dashboard():
        """Redirect to main dashboard."""
        return redirect("/")

    @app.route("/automations")
    def automations_page():
        """Serve automations builder page."""
        return send_from_directory(STATIC_DIR, "automations.html")

    @app.route("/api/status", methods=["GET"])
    def get_status():
        """Get overall system status."""
        stats = database.get_statistics()
        return jsonify({
            "status": "running",
            "bridge_connected": bridge.is_connected,
            "total_events": stats["total_events"],
            "active_patterns": stats["active_patterns"],
            "oldest_event": stats["oldest_event"].isoformat() if stats["oldest_event"] else None,
            "newest_event": stats["newest_event"].isoformat() if stats["newest_event"] else None,
            "timestamp": datetime.now().isoformat(),
        })

    @app.route("/api/lights", methods=["GET"])
    def get_lights():
        """Get current state of all lights."""
        lights = bridge.get_all_lights()
        return jsonify({
            "count": len(lights),
            "lights": [
                {
                    "id": light.light_id,
                    "name": light.name,
                    "is_on": light.is_on,
                    "brightness": light.brightness,
                    "brightness_percent": light.brightness_percent,
                    "hue": light.hue,
                    "saturation": light.saturation,
                    "color_temp": light.color_temp,
                    "reachable": light.reachable,
                }
                for light in lights.values()
            ],
        })

    @app.route("/api/rooms", methods=["GET"])
    def get_rooms():
        """Get all rooms."""
        rooms = bridge.get_all_rooms()
        return jsonify({
            "count": len(rooms),
            "rooms": [
                {
                    "id": room.room_id,
                    "name": room.name,
                    "light_ids": room.light_ids,
                }
                for room in rooms.values()
            ],
        })

    @app.route("/api/events", methods=["GET"])
    def get_events():
        """
        Get recent events.

        Query params:
            limit: Max number of events (default 100)
            light_id: Filter by light ID
            event_type: Filter by event type (on/off/brightness/color)
            days: Get events from last N days (default 7)
        """
        limit = request.args.get("limit", 100, type=int)
        light_id = request.args.get("light_id")
        event_type = request.args.get("event_type")
        days = request.args.get("days", 7, type=int)

        start_date = datetime.now() - timedelta(days=days)

        events = database.get_events(
            light_id=light_id,
            event_type=event_type,
            start_date=start_date,
            limit=limit,
        )

        return jsonify({
            "count": len(events),
            "events": [
                {
                    "id": e.id,
                    "light_id": e.light_id,
                    "light_name": e.light_name,
                    "timestamp": e.timestamp.isoformat(),
                    "event_type": e.event_type,
                    "old_value": e.old_value,
                    "new_value": e.new_value,
                    "weekday": e.weekday,
                    "hour": e.hour,
                }
                for e in events
            ],
        })

    @app.route("/api/events/summary", methods=["GET"])
    def get_events_summary():
        """Get summary of events by light and type."""
        days = request.args.get("days", 30, type=int)
        start_date = datetime.now() - timedelta(days=days)

        events = database.get_events(start_date=start_date, limit=10000)

        # Aggregate by light
        by_light = {}
        for e in events:
            if e.light_name not in by_light:
                by_light[e.light_name] = {"on": 0, "off": 0, "brightness": 0, "total": 0}
            by_light[e.light_name][e.event_type] = by_light[e.light_name].get(e.event_type, 0) + 1
            by_light[e.light_name]["total"] += 1

        # Aggregate by hour
        by_hour = {h: 0 for h in range(24)}
        for e in events:
            by_hour[e.hour] += 1

        return jsonify({
            "days_analyzed": days,
            "total_events": len(events),
            "by_light": by_light,
            "by_hour": by_hour,
        })

    @app.route("/api/patterns", methods=["GET"])
    def get_patterns():
        """Get detected patterns."""
        patterns = database.get_active_patterns()

        return jsonify({
            "count": len(patterns),
            "patterns": [
                {
                    "id": p.id,
                    "type": p.pattern_type,
                    "description": p.description,
                    "light_ids": p.light_ids.split(",") if p.light_ids else [],
                    "weekdays": [int(w) for w in p.weekdays.split(",") if w],
                    "time_start": p.time_start,
                    "time_end": p.time_end,
                    "confidence": p.confidence,
                    "occurrences": p.occurrence_count,
                    "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                    "is_active": p.is_active,
                }
                for p in patterns
            ],
        })

    @app.route("/api/analyze", methods=["POST"])
    def run_analysis():
        """Trigger pattern analysis manually."""
        days = request.args.get("days", 30, type=int)

        patterns = pattern_detector.analyze(days_back=days)

        # Save patterns
        for pattern in patterns:
            database.save_pattern(pattern)

        return jsonify({
            "success": True,
            "patterns_found": len(patterns),
            "summary": pattern_detector.get_pattern_summary(patterns),
        })

    @app.route("/api/health", methods=["GET"])
    def health_check():
        """Simple health check endpoint."""
        return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

    # ===== Automation Endpoints =====

    @app.route("/api/automations", methods=["GET"])
    def get_automations():
        """Get all automations."""
        automations = database.get_all_automations()
        return jsonify({
            "count": len(automations),
            "automations": [a.to_dict() for a in automations],
        })

    @app.route("/api/automations/<int:automation_id>", methods=["GET"])
    def get_automation(automation_id):
        """Get a single automation."""
        automation = database.get_automation(automation_id)
        if not automation:
            return jsonify({"error": "Automation not found"}), 404
        return jsonify(automation.to_dict())

    @app.route("/api/automations", methods=["POST"])
    def create_automation():
        """
        Create a new automation.

        Expected JSON body:
        {
            "name": "Wake up Hugo",
            "description": "Gradvis väckning",
            "trigger_type": "time",
            "trigger_config": {"time": "06:45", "weekdays": [1,2,3,4,5]},
            "target_type": "light",
            "target_ids": ["5", "6", "7"],
            "action_config": {"on": true, "bri": 254, "transitiontime": 6000}
        }
        """
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        required = ["name", "trigger_type", "trigger_config", "target_type", "target_ids", "action_config"]
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400

        automation = database.create_automation(
            name=data["name"],
            description=data.get("description", ""),
            trigger_type=data["trigger_type"],
            trigger_config=data["trigger_config"],
            target_type=data["target_type"],
            target_ids=data["target_ids"],
            action_config=data["action_config"],
        )

        return jsonify(automation.to_dict()), 201

    @app.route("/api/automations/<int:automation_id>", methods=["PUT"])
    def update_automation(automation_id):
        """Update an existing automation."""
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        automation = database.update_automation(automation_id, **data)
        if not automation:
            return jsonify({"error": "Automation not found"}), 404

        return jsonify(automation.to_dict())

    @app.route("/api/automations/<int:automation_id>", methods=["DELETE"])
    def delete_automation(automation_id):
        """Delete an automation."""
        success = database.delete_automation(automation_id)
        if not success:
            return jsonify({"error": "Automation not found"}), 404
        return jsonify({"success": True})

    @app.route("/api/automations/<int:automation_id>/toggle", methods=["POST"])
    def toggle_automation(automation_id):
        """Toggle automation enabled/disabled."""
        automation = database.toggle_automation(automation_id)
        if not automation:
            return jsonify({"error": "Automation not found"}), 404
        return jsonify(automation.to_dict())

    @app.route("/api/automations/<int:automation_id>/run", methods=["POST"])
    def run_automation(automation_id):
        """Manually trigger an automation."""
        automation = database.get_automation(automation_id)
        if not automation:
            return jsonify({"error": "Automation not found"}), 404

        # Execute the automation
        import json
        action = json.loads(automation.action_config) if automation.action_config else {}
        target_ids = automation.target_ids.split(",") if automation.target_ids else []

        # Map Hue API param names to method param names
        param_map = {
            "bri": "brightness",
            "sat": "saturation",
            "ct": "color_temp",
            "transitiontime": "transition_time",
        }
        mapped_action = {}
        for key, value in action.items():
            mapped_key = param_map.get(key, key)
            mapped_action[mapped_key] = value

        success_count = 0
        for target_id in target_ids:
            target_id = target_id.strip()
            if automation.target_type == "light":
                if bridge.set_light_state(target_id, **mapped_action):
                    success_count += 1
            elif automation.target_type == "room":
                if bridge.set_group_state(target_id, **mapped_action):
                    success_count += 1

        # Record trigger
        database.record_automation_trigger(automation_id)

        return jsonify({
            "success": True,
            "automation": automation.name,
            "targets_updated": success_count,
            "total_targets": len(target_ids),
        })

    @app.route("/api/scenes", methods=["GET"])
    def get_scenes():
        """Get all Hue scenes."""
        scenes = bridge.get_scenes()
        return jsonify({
            "count": len(scenes),
            "scenes": [
                {
                    "id": scene_id,
                    "name": scene_data.get("name", "Unknown"),
                    "lights": scene_data.get("lights", []),
                    "type": scene_data.get("type", ""),
                }
                for scene_id, scene_data in scenes.items()
            ],
        })

    @app.route("/api/lights/<light_id>/state", methods=["PUT"])
    def set_light_state(light_id):
        """
        Set light state directly (for testing/manual control).

        JSON body can include: on, bri, hue, sat, ct, transitiontime, alert, effect, xy
        """
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        success = bridge.set_light_state(
            light_id,
            on=data.get("on"),
            brightness=data.get("bri"),
            hue=data.get("hue"),
            saturation=data.get("sat"),
            color_temp=data.get("ct"),
            transition_time=data.get("transitiontime"),
            alert=data.get("alert"),
            effect=data.get("effect"),
            xy=data.get("xy"),
        )

        return jsonify({"success": success})

    @app.route("/api/groups/<group_id>/state", methods=["PUT"])
    def set_group_state(group_id):
        """Set group/room state directly."""
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        success = bridge.set_group_state(
            group_id,
            on=data.get("on"),
            brightness=data.get("bri"),
            hue=data.get("hue"),
            saturation=data.get("sat"),
            color_temp=data.get("ct"),
            transition_time=data.get("transitiontime"),
            alert=data.get("alert"),
            effect=data.get("effect"),
            xy=data.get("xy"),
            scene=data.get("scene"),
        )

        return jsonify({"success": success})

    @app.route("/api/sun", methods=["GET"])
    def get_sun_times():
        """Get today's sunrise and sunset times."""
        from src.automation.executor import SunCalculator
        sun = SunCalculator()
        return jsonify({
            "sunrise": sun.get_sunrise().strftime("%H:%M"),
            "sunset": sun.get_sunset().strftime("%H:%M"),
            "date": datetime.now().strftime("%Y-%m-%d"),
        })

    return app


class APIServer:
    """Runs Flask API in a background thread."""

    def __init__(
        self,
        database: Database,
        bridge: HueBridge,
        pattern_detector: PatternDetector,
        host: str = "0.0.0.0",
        port: int = 5000,
    ):
        """
        Initialize API server.

        Args:
            database: Database instance
            bridge: HueBridge instance
            pattern_detector: PatternDetector instance
            host: Host to bind to (0.0.0.0 for all interfaces)
            port: Port to listen on
        """
        self.app = create_api(database, bridge, pattern_detector)
        self.host = host
        self.port = port
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start API server in background thread."""
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="api-server",
        )
        self._thread.start()
        logger.info(f"🌐 API server started on http://{self.host}:{self.port}")
        logger.info(f"📊 Dashboard: http://{self.host}:{self.port}/")

    def _run(self):
        """Run Flask app (called in background thread)."""
        # Suppress Flask's default logging
        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.WARNING)

        self.app.run(
            host=self.host,
            port=self.port,
            debug=False,
            use_reloader=False,
        )
