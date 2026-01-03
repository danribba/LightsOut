"""Main service orchestrating all components."""

import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from src.hue.bridge import HueBridge
from src.storage.database import Database
from src.storage.event_logger import EventLogger
from src.analyzer.pattern_detector import PatternDetector
from src.analyzer.predictor import LightingPredictor


class HueAnalyzerService:
    """Main service that monitors lights and detects patterns."""

    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialize the service.

        Args:
            config_path: Path to configuration file.
        """
        self.config = self._load_config(config_path)
        self._setup_logging()

        # Initialize components
        self.bridge = HueBridge(self.config["hue"].get("bridge_ip"))
        self.database = Database(self.config["storage"]["database_path"])
        self.event_logger = EventLogger(self.database)
        self.pattern_detector = PatternDetector(
            self.database,
            min_occurrences=self.config["analyzer"]["min_pattern_occurrences"],
            time_window_minutes=self.config["analyzer"]["time_window_minutes"],
            confidence_threshold=self.config["analyzer"]["confidence_threshold"],
        )
        self.predictor = LightingPredictor(
            self.database,
            min_confidence=self.config["automation"]["min_confidence"],
        )

        self.scheduler = BackgroundScheduler()
        self._running = False

    def _load_config(self, config_path: str) -> dict:
        """Load configuration from YAML file."""
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"Config file not found: {config_path}, using defaults")
            return self._default_config()

        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _default_config(self) -> dict:
        """Return default configuration."""
        return {
            "hue": {"bridge_ip": "", "poll_interval": 10},
            "storage": {"database_path": "data/hue_events.db", "retention_days": 90},
            "analyzer": {
                "min_pattern_occurrences": 3,
                "time_window_minutes": 15,
                "confidence_threshold": 0.7,
                "analysis_window_days": 30,
            },
            "logging": {"level": "INFO", "file_path": "logs/hue_analyzer.log"},
            "automation": {"enabled": False, "dry_run": True, "min_confidence": 0.85},
        }

    def _setup_logging(self):
        """Configure logging."""
        log_config = self.config.get("logging", {})
        log_path = Path(log_config.get("file_path", "logs/hue_analyzer.log"))
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove default handler
        logger.remove()

        # Add console handler
        logger.add(
            sys.stderr,
            level=log_config.get("level", "INFO"),
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        )

        # Add file handler
        logger.add(
            str(log_path),
            level="DEBUG",
            rotation=f"{log_config.get('max_size_mb', 10)} MB",
            retention=log_config.get("backup_count", 5),
        )

    def start(self):
        """Start the service."""
        logger.info("🚀 Starting LightsOut Hue Analyzer...")

        # Connect to bridge
        if not self.bridge.connect():
            logger.error("Failed to connect to Hue Bridge. Exiting.")
            sys.exit(1)

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        # Schedule jobs
        poll_interval = self.config["hue"]["poll_interval"]
        self.scheduler.add_job(
            self._poll_lights,
            "interval",
            seconds=poll_interval,
            id="poll_lights",
        )

        # Run analysis daily at 3 AM
        self.scheduler.add_job(
            self._run_analysis,
            "cron",
            hour=3,
            id="daily_analysis",
        )

        # Cleanup old data weekly
        self.scheduler.add_job(
            self._cleanup_data,
            "cron",
            day_of_week="sun",
            hour=4,
            id="weekly_cleanup",
        )

        self.scheduler.start()
        self._running = True

        logger.info(f"✅ Service started. Polling every {poll_interval}s")
        self._print_status()

        # Keep main thread alive
        while self._running:
            time.sleep(1)

    def _poll_lights(self):
        """Poll lights for changes."""
        try:
            changes = self.bridge.detect_changes()

            for old_state, new_state in changes:
                events = self.event_logger.log_state_change(old_state, new_state)

                # Check for sequence triggers (if automation enabled)
                if self.config["automation"]["enabled"]:
                    for event in events:
                        self._handle_automation(event)

        except Exception as e:
            logger.error(f"Error polling lights: {e}")

    def _handle_automation(self, event):
        """Handle automation based on event."""
        actions = self.predictor.should_trigger_sequence(
            event.light_id, event.event_type
        )

        for action in actions:
            if self.config["automation"]["dry_run"]:
                logger.info(f"[DRY RUN] Would trigger: {action}")
            else:
                # Actually trigger the action
                time.sleep(action.get("delay_seconds", 0))
                if action["action"] == "on":
                    self.bridge.set_light_state(action["light_id"], on=True)
                elif action["action"] == "off":
                    self.bridge.set_light_state(action["light_id"], on=False)
                logger.info(f"Triggered automation: {action}")

    def _run_analysis(self):
        """Run pattern analysis."""
        logger.info("🔍 Running pattern analysis...")
        try:
            days = self.config["analyzer"]["analysis_window_days"]
            patterns = self.pattern_detector.analyze(days_back=days)

            # Save new patterns
            for pattern in patterns:
                self.database.save_pattern(pattern)

            summary = self.pattern_detector.get_pattern_summary(patterns)
            logger.info(f"\n{summary}")

        except Exception as e:
            logger.error(f"Error running analysis: {e}")

    def _cleanup_data(self):
        """Clean up old data."""
        retention = self.config["storage"]["retention_days"]
        logger.info(f"🧹 Cleaning up data older than {retention} days...")
        self.database.cleanup_old_events(retention)

    def _print_status(self):
        """Print current status."""
        lights = self.bridge.get_all_lights()
        rooms = self.bridge.get_all_rooms()
        stats = self.database.get_statistics()

        logger.info(f"📡 Connected lights: {len(lights)}")
        logger.info(f"🏠 Rooms: {len(rooms)}")
        logger.info(f"📊 Total events logged: {stats['total_events']}")
        logger.info(f"🎯 Active patterns: {stats['active_patterns']}")

    def _shutdown(self, signum, frame):
        """Graceful shutdown."""
        logger.info("🛑 Shutting down...")
        self._running = False
        self.scheduler.shutdown(wait=False)
        logger.info("👋 Goodbye!")
        sys.exit(0)

    def run_analysis_now(self):
        """Manually trigger analysis."""
        self._run_analysis()

    def get_status(self) -> dict:
        """Get current service status."""
        return {
            "running": self._running,
            "bridge_connected": self.bridge.is_connected,
            "database_stats": self.database.get_statistics(),
            "events_this_session": self.event_logger.total_events_logged,
        }
