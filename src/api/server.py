"""REST API server for remote access to LightsOut data."""

import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory, redirect
from flask_cors import CORS
from loguru import logger
import threading
import time
import math

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

    # ===== Hue Bridge Native Automations =====

    @app.route("/api/hue/schedules", methods=["GET"])
    def get_hue_schedules():
        """Get all schedules from Hue bridge."""
        schedules = bridge.get_schedules()
        return jsonify({
            "count": len(schedules),
            "schedules": [
                {
                    "id": sid,
                    "name": s.get("name", "Unknown"),
                    "description": s.get("description", ""),
                    "status": s.get("status", "disabled"),
                    "localtime": s.get("localtime", ""),
                    "command": s.get("command", {}),
                    "created": s.get("created", ""),
                    "starttime": s.get("starttime", ""),
                }
                for sid, s in schedules.items()
            ],
        })

    @app.route("/api/hue/schedules/<schedule_id>", methods=["GET"])
    def get_hue_schedule(schedule_id):
        """Get a specific schedule from Hue bridge."""
        schedule = bridge.get_schedule_details(schedule_id)
        if not schedule:
            return jsonify({"error": "Schedule not found"}), 404
        return jsonify({
            "id": schedule_id,
            **schedule,
        })

    @app.route("/api/hue/schedules/<schedule_id>", methods=["PUT"])
    def update_hue_schedule(schedule_id):
        """
        Update a schedule on the Hue bridge.

        JSON body can include: name, description, status, localtime, command
        """
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # If updating command and transitiontime is provided, merge it
        if "transitiontime" in data and "command" not in data:
            # Get existing schedule to merge transitiontime into command
            existing = bridge.get_schedule_details(schedule_id)
            if existing and "command" in existing:
                command = existing["command"].copy()
                body = command.get("body", {})
                body["transitiontime"] = data["transitiontime"]
                command["body"] = body
                data["command"] = command
            del data["transitiontime"]

        success = bridge.update_schedule(schedule_id, **data)
        if not success:
            return jsonify({"error": "Failed to update schedule"}), 500

        return jsonify({"success": True, "id": schedule_id})

    @app.route("/api/hue/schedules/<schedule_id>", methods=["DELETE"])
    def delete_hue_schedule(schedule_id):
        """Delete a schedule from the Hue bridge."""
        success = bridge.delete_schedule(schedule_id)
        if not success:
            return jsonify({"error": "Failed to delete schedule"}), 500
        return jsonify({"success": True})

    @app.route("/api/hue/schedules", methods=["POST"])
    def create_hue_schedule():
        """
        Create a new schedule on the Hue bridge.

        Required JSON body:
        {
            "name": "Schedule name",
            "command": {
                "address": "/api/<user>/lights/1/state",
                "method": "PUT",
                "body": {"on": true, "bri": 254}
            },
            "localtime": "W124/T07:00:00"
        }

        Optional: description, status, autodelete
        """
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        required = ["name", "command", "localtime"]
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400

        schedule_id = bridge.create_schedule(
            name=data["name"],
            command=data["command"],
            localtime=data["localtime"],
            description=data.get("description", ""),
            status=data.get("status", "enabled"),
            autodelete=data.get("autodelete", False),
        )

        if not schedule_id:
            return jsonify({"error": "Failed to create schedule"}), 500

        return jsonify({"success": True, "id": schedule_id}), 201

    @app.route("/api/hue/rules", methods=["GET"])
    def get_hue_rules():
        """Get all rules from Hue bridge."""
        rules = bridge.get_rules()
        return jsonify({
            "count": len(rules),
            "rules": [
                {
                    "id": rid,
                    "name": r.get("name", "Unknown"),
                    "status": r.get("status", "disabled"),
                    "conditions": r.get("conditions", []),
                    "actions": r.get("actions", []),
                    "owner": r.get("owner", ""),
                    "times_triggered": r.get("timestriggered", 0),
                    "last_triggered": r.get("lasttriggered", ""),
                    "created": r.get("created", ""),
                }
                for rid, r in rules.items()
            ],
        })

    @app.route("/api/hue/sensors", methods=["GET"])
    def get_hue_sensors():
        """Get all sensors from Hue bridge."""
        sensors = bridge.get_sensors()
        return jsonify({
            "count": len(sensors),
            "sensors": [
                {
                    "id": sid,
                    "name": s.get("name", "Unknown"),
                    "type": s.get("type", ""),
                    "model_id": s.get("modelid", ""),
                    "manufacturer": s.get("manufacturername", ""),
                    "state": s.get("state", {}),
                    "config": s.get("config", {}),
                }
                for sid, s in sensors.items()
            ],
        })

    # ===== Adaptive Lighting =====

    # Store for active adaptive lighting sessions
    adaptive_sessions = {}

    def lightlevel_to_lux(lightlevel):
        """Convert Hue lightlevel to lux. Formula: lux = 10^((lightlevel - 1) / 10000)"""
        if lightlevel <= 0:
            return 0
        return round(10 ** ((lightlevel - 1) / 10000), 1)

    def lux_to_lightlevel(lux):
        """Convert lux to Hue lightlevel."""
        if lux <= 0:
            return 0
        return int(math.log10(lux) * 10000 + 1)

    @app.route("/api/hue/lightsensors", methods=["GET"])
    def get_light_sensors():
        """Get all ambient light sensors with current readings."""
        sensors = bridge.get_sensors()
        light_sensors = []

        for sid, s in sensors.items():
            if s.get("type") == "ZLLLightLevel":
                state = s.get("state", {})
                lightlevel = state.get("lightlevel", 0)
                light_sensors.append({
                    "id": sid,
                    "name": s.get("name", "Unknown"),
                    "lightlevel": lightlevel,
                    "lux": lightlevel_to_lux(lightlevel),
                    "dark": state.get("dark", False),
                    "daylight": state.get("daylight", False),
                    "lastupdated": state.get("lastupdated", ""),
                })

        return jsonify({
            "count": len(light_sensors),
            "sensors": light_sensors,
        })

    @app.route("/api/adaptive/start", methods=["POST"])
    def start_adaptive_lighting():
        """
        Start adaptive lighting test.

        JSON body:
        {
            "sensor_id": "42",
            "light_ids": ["1", "2"],
            "target_lux": 150,
            "min_brightness": 1,
            "max_brightness": 254,
            "step": 10
        }
        """
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        sensor_id = data.get("sensor_id")
        light_ids = data.get("light_ids", [])
        target_lux = data.get("target_lux", 150)
        min_brightness = data.get("min_brightness", 1)
        max_brightness = data.get("max_brightness", 254)
        step = data.get("step", 10)

        if not sensor_id or not light_ids:
            return jsonify({"error": "sensor_id and light_ids required"}), 400

        session_id = f"adaptive_{sensor_id}"

        # Stop existing session if any
        if session_id in adaptive_sessions:
            adaptive_sessions[session_id]["active"] = False
            time.sleep(0.5)

        # Create new session
        session = {
            "active": True,
            "sensor_id": sensor_id,
            "light_ids": light_ids,
            "target_lux": target_lux,
            "min_brightness": min_brightness,
            "max_brightness": max_brightness,
            "step": step,
            "current_brightness": 0,
            "current_lux": 0,
            "iterations": 0,
            "status": "starting",
        }
        adaptive_sessions[session_id] = session

        # Start background thread
        thread = threading.Thread(
            target=_run_adaptive_loop,
            args=(bridge, session_id, adaptive_sessions),
            daemon=True,
        )
        thread.start()

        return jsonify({
            "success": True,
            "session_id": session_id,
            "message": f"Started adaptive lighting for sensor {sensor_id}",
        })

    @app.route("/api/adaptive/stop", methods=["POST"])
    def stop_adaptive_lighting():
        """Stop adaptive lighting test."""
        data = request.get_json() or {}
        session_id = data.get("session_id")

        if session_id and session_id in adaptive_sessions:
            adaptive_sessions[session_id]["active"] = False
            adaptive_sessions[session_id]["status"] = "stopped"
            return jsonify({"success": True, "message": "Stopped"})

        # Stop all sessions
        for sid in adaptive_sessions:
            adaptive_sessions[sid]["active"] = False
            adaptive_sessions[sid]["status"] = "stopped"

        return jsonify({"success": True, "message": "All sessions stopped"})

    @app.route("/api/adaptive/status", methods=["GET"])
    def get_adaptive_status():
        """Get status of all adaptive lighting sessions."""
        # Also refresh sensor readings
        sensors = bridge.get_sensors()

        sessions_status = []
        for session_id, session in adaptive_sessions.items():
            # Get fresh sensor reading
            sensor = sensors.get(session["sensor_id"], {})
            state = sensor.get("state", {})
            lightlevel = state.get("lightlevel", 0)

            sessions_status.append({
                "session_id": session_id,
                "active": session["active"],
                "sensor_id": session["sensor_id"],
                "light_ids": session["light_ids"],
                "target_lux": session["target_lux"],
                "current_lux": lightlevel_to_lux(lightlevel),
                "current_brightness": session.get("current_brightness", 0),
                "iterations": session.get("iterations", 0),
                "status": session.get("status", "unknown"),
            })

        return jsonify({"sessions": sessions_status})

    @app.route("/api/adaptive/test-once", methods=["POST"])
    def test_adaptive_once():
        """
        Run a single adaptive adjustment iteration.
        Useful for testing without starting a continuous loop.
        """
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        sensor_id = data.get("sensor_id")
        light_ids = data.get("light_ids", [])
        target_lux = data.get("target_lux", 150)

        if not sensor_id or not light_ids:
            return jsonify({"error": "sensor_id and light_ids required"}), 400

        # Get current sensor reading
        sensors = bridge.get_sensors()
        sensor = sensors.get(sensor_id, {})
        state = sensor.get("state", {})
        lightlevel = state.get("lightlevel", 0)
        current_lux = lightlevel_to_lux(lightlevel)

        # Get current light brightness (use first light)
        lights = bridge.get_all_lights()
        first_light = lights.get(light_ids[0])
        current_bri = first_light.brightness if first_light and first_light.is_on else 0

        # Calculate adjustment
        lux_diff = target_lux - current_lux

        if abs(lux_diff) < 5:  # Close enough
            return jsonify({
                "action": "none",
                "reason": "Target reached",
                "current_lux": current_lux,
                "target_lux": target_lux,
                "current_brightness": current_bri,
            })

        # Estimate brightness adjustment needed
        # Rough estimate: 10 lux ≈ 5 brightness units
        adjustment = int(lux_diff / 2)
        adjustment = max(-25, min(25, adjustment))  # Limit step size

        new_bri = current_bri + adjustment
        new_bri = max(1, min(254, new_bri))

        # Apply to all lights
        for light_id in light_ids:
            bridge.set_light_state(light_id, on=True, brightness=new_bri)

        return jsonify({
            "action": "adjusted",
            "current_lux": current_lux,
            "target_lux": target_lux,
            "lux_diff": lux_diff,
            "previous_brightness": current_bri,
            "new_brightness": new_bri,
            "adjustment": adjustment,
            "lights_updated": light_ids,
        })

    return app


def _run_adaptive_loop(bridge, session_id, sessions):
    """Background thread for adaptive lighting adjustment."""
    session = sessions.get(session_id)
    if not session:
        return

    session["status"] = "running"
    logger.info(f"Starting adaptive lighting loop: {session_id}")

    # Give lights time to turn on initially
    for light_id in session["light_ids"]:
        bridge.set_light_state(light_id, on=True, brightness=session["min_brightness"])
    time.sleep(2)

    while session.get("active", False):
        try:
            # Get current sensor reading
            sensors = bridge.get_sensors()
            sensor = sensors.get(session["sensor_id"], {})
            state = sensor.get("state", {})
            lightlevel = state.get("lightlevel", 0)
            current_lux = 10 ** ((lightlevel - 1) / 10000) if lightlevel > 0 else 0

            session["current_lux"] = round(current_lux, 1)
            session["iterations"] += 1

            # Get current brightness from first light
            lights = bridge.get_all_lights()
            first_light = lights.get(session["light_ids"][0])
            current_bri = first_light.brightness if first_light and first_light.is_on else session["min_brightness"]
            session["current_brightness"] = current_bri

            target_lux = session["target_lux"]
            lux_diff = target_lux - current_lux

            # Check if we've reached target
            if abs(lux_diff) < 5:
                session["status"] = "target_reached"
                logger.info(f"Adaptive {session_id}: Target reached at {current_lux} lux, brightness {current_bri}")
            else:
                session["status"] = "adjusting"

                # Calculate adjustment
                adjustment = int(lux_diff / 2)
                adjustment = max(-session["step"], min(session["step"], adjustment))

                new_bri = current_bri + adjustment
                new_bri = max(session["min_brightness"], min(session["max_brightness"], new_bri))

                if new_bri != current_bri:
                    for light_id in session["light_ids"]:
                        bridge.set_light_state(light_id, brightness=new_bri)
                    logger.debug(f"Adaptive {session_id}: lux={current_lux:.1f}, target={target_lux}, bri {current_bri}→{new_bri}")

            # Wait before next iteration
            time.sleep(3)

        except Exception as e:
            logger.error(f"Adaptive lighting error: {e}")
            session["status"] = f"error: {e}"
            time.sleep(5)

    session["status"] = "stopped"
    logger.info(f"Adaptive lighting stopped: {session_id}")


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
