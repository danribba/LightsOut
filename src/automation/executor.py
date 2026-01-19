"""Automation executor - schedules and runs automations."""

import json
import math
from datetime import datetime, time, timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from src.hue.bridge import HueBridge
from src.storage.database import Database, Automation


class SunCalculator:
    """Calculate sunrise and sunset times for a location."""

    def __init__(self, latitude: float = 59.3293, longitude: float = 18.0686):
        """
        Initialize with location coordinates.

        Default: Stockholm, Sweden
        """
        self.latitude = latitude
        self.longitude = longitude

    def get_sunrise(self, date: Optional[datetime] = None) -> time:
        """Get sunrise time for a given date."""
        return self._calculate_sun_time(date or datetime.now(), rising=True)

    def get_sunset(self, date: Optional[datetime] = None) -> time:
        """Get sunset time for a given date."""
        return self._calculate_sun_time(date or datetime.now(), rising=False)

    def _calculate_sun_time(self, date: datetime, rising: bool) -> time:
        """
        Calculate sunrise or sunset using simplified algorithm.

        Based on NOAA solar calculations (simplified).
        """
        # Day of year
        n = date.timetuple().tm_yday

        # Solar noon approximation
        lng_hour = self.longitude / 15

        if rising:
            t = n + ((6 - lng_hour) / 24)
        else:
            t = n + ((18 - lng_hour) / 24)

        # Sun's mean anomaly
        M = (0.9856 * t) - 3.289

        # Sun's true longitude
        L = M + (1.916 * math.sin(math.radians(M))) + (0.020 * math.sin(math.radians(2 * M))) + 282.634
        L = L % 360

        # Sun's right ascension
        RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L))))
        RA = RA % 360

        # Right ascension adjustment
        L_quadrant = (math.floor(L / 90)) * 90
        RA_quadrant = (math.floor(RA / 90)) * 90
        RA = RA + (L_quadrant - RA_quadrant)
        RA = RA / 15  # Convert to hours

        # Sun's declination
        sin_dec = 0.39782 * math.sin(math.radians(L))
        cos_dec = math.cos(math.asin(sin_dec))

        # Sun's local hour angle
        zenith = 90.833  # Official zenith for sunrise/sunset
        cos_H = (math.cos(math.radians(zenith)) - (sin_dec * math.sin(math.radians(self.latitude)))) / (
            cos_dec * math.cos(math.radians(self.latitude))
        )

        # Clamp to valid range
        cos_H = max(-1, min(1, cos_H))

        if rising:
            H = 360 - math.degrees(math.acos(cos_H))
        else:
            H = math.degrees(math.acos(cos_H))

        H = H / 15  # Convert to hours

        # Local mean time
        T = H + RA - (0.06571 * t) - 6.622

        # UTC time
        UT = T - lng_hour
        UT = UT % 24

        # Convert to local time (approximate, assumes UTC+1 for Sweden)
        # TODO: Use proper timezone handling
        local_hour = (UT + 1) % 24

        hour = int(local_hour)
        minute = int((local_hour - hour) * 60)

        return time(hour=hour, minute=minute)


class AutomationExecutor:
    """Executes automations based on their triggers."""

    def __init__(
        self,
        database: Database,
        bridge: HueBridge,
        latitude: float = 59.3293,
        longitude: float = 18.0686,
    ):
        """
        Initialize automation executor.

        Args:
            database: Database instance
            bridge: HueBridge instance
            latitude: Location latitude for sun calculations
            longitude: Location longitude for sun calculations
        """
        self.database = database
        self.bridge = bridge
        self.sun = SunCalculator(latitude, longitude)
        self.scheduler = BackgroundScheduler()
        self._scheduled_jobs: dict[int, str] = {}  # automation_id -> job_id

    def start(self):
        """Start the automation scheduler."""
        self.scheduler.start()
        self.reload_automations()
        logger.info("Automation executor started")

    def stop(self):
        """Stop the automation scheduler."""
        self.scheduler.shutdown(wait=False)
        logger.info("Automation executor stopped")

    def reload_automations(self):
        """Reload all automations from database and reschedule."""
        # Remove existing jobs
        for job_id in self._scheduled_jobs.values():
            try:
                self.scheduler.remove_job(job_id)
            except Exception:
                pass
        self._scheduled_jobs.clear()

        # Load enabled automations
        automations = self.database.get_enabled_automations()
        for automation in automations:
            self._schedule_automation(automation)

        logger.info(f"Loaded {len(automations)} automations")

    def _schedule_automation(self, automation: Automation):
        """Schedule a single automation based on its trigger type."""
        trigger_config = json.loads(automation.trigger_config) if automation.trigger_config else {}

        if automation.trigger_type == "time":
            self._schedule_time_trigger(automation, trigger_config)
        elif automation.trigger_type == "sunrise":
            self._schedule_sun_trigger(automation, trigger_config, is_sunrise=True)
        elif automation.trigger_type == "sunset":
            self._schedule_sun_trigger(automation, trigger_config, is_sunrise=False)
        # "manual" type doesn't need scheduling

    def _schedule_time_trigger(self, automation: Automation, config: dict):
        """Schedule a time-based trigger."""
        time_str = config.get("time", "00:00")
        weekdays = config.get("weekdays", [0, 1, 2, 3, 4, 5, 6])  # Default: all days

        try:
            hour, minute = map(int, time_str.split(":"))
        except ValueError:
            logger.error(f"Invalid time format for automation {automation.id}: {time_str}")
            return

        # Convert weekdays to cron format (0=Mon in our system, but cron uses 0=Sun)
        # APScheduler uses 0-6 where 0=Monday, same as Python's weekday()
        day_of_week = ",".join(str(d) for d in weekdays)

        trigger = CronTrigger(
            hour=hour,
            minute=minute,
            day_of_week=day_of_week,
        )

        job = self.scheduler.add_job(
            self._execute_automation,
            trigger,
            args=[automation.id],
            id=f"automation_{automation.id}",
            replace_existing=True,
        )

        self._scheduled_jobs[automation.id] = job.id
        logger.debug(f"Scheduled automation '{automation.name}' at {time_str} on days {weekdays}")

    def _schedule_sun_trigger(self, automation: Automation, config: dict, is_sunrise: bool):
        """Schedule a sunrise/sunset-based trigger."""
        offset_minutes = config.get("offset_minutes", 0)

        # Get today's sun time
        if is_sunrise:
            sun_time = self.sun.get_sunrise()
        else:
            sun_time = self.sun.get_sunset()

        # Apply offset
        sun_datetime = datetime.combine(datetime.today(), sun_time)
        trigger_datetime = sun_datetime + timedelta(minutes=offset_minutes)
        trigger_time = trigger_datetime.time()

        weekdays = config.get("weekdays", [0, 1, 2, 3, 4, 5, 6])
        day_of_week = ",".join(str(d) for d in weekdays)

        trigger = CronTrigger(
            hour=trigger_time.hour,
            minute=trigger_time.minute,
            day_of_week=day_of_week,
        )

        job = self.scheduler.add_job(
            self._execute_automation,
            trigger,
            args=[automation.id],
            id=f"automation_{automation.id}",
            replace_existing=True,
        )

        self._scheduled_jobs[automation.id] = job.id
        sun_type = "sunrise" if is_sunrise else "sunset"
        logger.debug(
            f"Scheduled automation '{automation.name}' at {sun_type} "
            f"({trigger_time.strftime('%H:%M')}, offset {offset_minutes}min)"
        )

    def _execute_automation(self, automation_id: int):
        """Execute an automation by ID."""
        automation = self.database.get_automation(automation_id)
        if not automation or not automation.is_enabled:
            logger.warning(f"Automation {automation_id} not found or disabled")
            return

        logger.info(f"Executing automation: {automation.name}")

        action = json.loads(automation.action_config) if automation.action_config else {}
        target_ids = automation.target_ids.split(",") if automation.target_ids else []

        # Handle sequences (multiple actions with delays)
        if "sequence" in action:
            self._execute_sequence(automation, action["sequence"])
        else:
            # Single action
            self._execute_action(automation.target_type, target_ids, action)

        # Record trigger
        self.database.record_automation_trigger(automation_id)

    def _execute_action(self, target_type: str, target_ids: list[str], action: dict):
        """Execute a single action on targets."""
        for target_id in target_ids:
            target_id = target_id.strip()
            try:
                if target_type == "light":
                    self.bridge.set_light_state(
                        target_id,
                        on=action.get("on"),
                        brightness=action.get("bri"),
                        hue=action.get("hue"),
                        saturation=action.get("sat"),
                        color_temp=action.get("ct"),
                        transition_time=action.get("transitiontime"),
                        alert=action.get("alert"),
                        effect=action.get("effect"),
                        xy=action.get("xy"),
                    )
                elif target_type == "room":
                    self.bridge.set_group_state(
                        target_id,
                        on=action.get("on"),
                        brightness=action.get("bri"),
                        hue=action.get("hue"),
                        saturation=action.get("sat"),
                        color_temp=action.get("ct"),
                        transition_time=action.get("transitiontime"),
                        alert=action.get("alert"),
                        effect=action.get("effect"),
                        xy=action.get("xy"),
                        scene=action.get("scene"),
                    )
                logger.debug(f"Executed action on {target_type} {target_id}")
            except Exception as e:
                logger.error(f"Failed to execute action on {target_type} {target_id}: {e}")

    def _execute_sequence(self, automation: Automation, sequence: list[dict]):
        """
        Execute a sequence of actions with delays.

        Sequence format:
        [
            {"delay": 0, "action": {"on": true, "bri": 1}},
            {"delay": 60, "action": {"bri": 127}},
            {"delay": 120, "action": {"bri": 254}}
        ]
        """
        target_type = automation.target_type
        target_ids = automation.target_ids.split(",") if automation.target_ids else []

        for step in sequence:
            delay = step.get("delay", 0)
            action = step.get("action", {})

            if delay > 0:
                # Schedule delayed execution
                run_time = datetime.now() + timedelta(seconds=delay)
                self.scheduler.add_job(
                    self._execute_action,
                    "date",
                    run_date=run_time,
                    args=[target_type, target_ids, action],
                )
            else:
                # Execute immediately
                self._execute_action(target_type, target_ids, action)

        logger.info(f"Scheduled {len(sequence)} steps for sequence automation")

    def get_next_sun_times(self) -> dict:
        """Get today's sunrise and sunset times."""
        return {
            "sunrise": self.sun.get_sunrise().strftime("%H:%M"),
            "sunset": self.sun.get_sunset().strftime("%H:%M"),
            "date": datetime.now().strftime("%Y-%m-%d"),
        }
