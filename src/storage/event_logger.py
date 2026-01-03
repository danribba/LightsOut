"""Event logger for tracking light state changes."""

from datetime import datetime
from typing import Optional

from loguru import logger

from src.hue.models import LightEvent, LightState
from .database import Database


class EventLogger:
    """Logs light events to database."""

    def __init__(self, database: Database):
        """
        Initialize event logger.

        Args:
            database: Database instance for storage.
        """
        self.db = database
        self._event_count = 0

    def log_state_change(
        self,
        old_state: LightState,
        new_state: LightState,
    ) -> list[LightEvent]:
        """
        Log all changes between two light states.

        Args:
            old_state: Previous light state
            new_state: Current light state

        Returns:
            List of logged events.
        """
        events = []
        timestamp = datetime.now()

        # Check on/off change
        if old_state.is_on != new_state.is_on:
            event_type = "on" if new_state.is_on else "off"
            event = self._create_event(
                new_state,
                event_type,
                str(old_state.is_on),
                str(new_state.is_on),
                timestamp,
            )
            events.append(event)
            logger.info(
                f"💡 {new_state.name}: {'Tänd' if new_state.is_on else 'Släckt'}"
            )

        # Only check other changes if light is on
        if new_state.is_on:
            # Brightness change
            if abs(old_state.brightness - new_state.brightness) > 5:
                event = self._create_event(
                    new_state,
                    "brightness",
                    str(old_state.brightness_percent),
                    str(new_state.brightness_percent),
                    timestamp,
                )
                events.append(event)
                logger.info(
                    f"🔆 {new_state.name}: Ljusstyrka "
                    f"{old_state.brightness_percent}% → {new_state.brightness_percent}%"
                )

            # Hue/color change
            if (
                old_state.hue is not None
                and new_state.hue is not None
                and abs(old_state.hue - new_state.hue) > 1000
            ):
                event = self._create_event(
                    new_state,
                    "hue",
                    str(old_state.hue),
                    str(new_state.hue),
                    timestamp,
                )
                events.append(event)
                logger.info(f"🎨 {new_state.name}: Färg ändrad")

            # Color temperature change
            if (
                old_state.color_temp is not None
                and new_state.color_temp is not None
                and abs(old_state.color_temp - new_state.color_temp) > 10
            ):
                event = self._create_event(
                    new_state,
                    "color_temp",
                    str(old_state.color_temp),
                    str(new_state.color_temp),
                    timestamp,
                )
                events.append(event)
                logger.info(f"🌡️ {new_state.name}: Färgtemperatur ändrad")

        self._event_count += len(events)
        return events

    def _create_event(
        self,
        light_state: LightState,
        event_type: str,
        old_value: str,
        new_value: str,
        timestamp: datetime,
    ) -> LightEvent:
        """Create and save an event."""
        # Save to database
        self.db.add_event(
            light_id=light_state.light_id,
            light_name=light_state.name,
            event_type=event_type,
            old_value=old_value,
            new_value=new_value,
            timestamp=timestamp,
        )

        # Return event object
        return LightEvent(
            light_id=light_state.light_id,
            light_name=light_state.name,
            timestamp=timestamp,
            event_type=event_type,
            old_value=old_value,
            new_value=new_value,
            weekday=timestamp.weekday(),
            hour=timestamp.hour,
            minute=timestamp.minute,
        )

    def log_snapshot(self, lights: dict[str, LightState]):
        """
        Save a snapshot of all light states.

        Args:
            lights: Dictionary of light_id to LightState.
        """
        for light_state in lights.values():
            self.db.add_snapshot(light_state.to_dict())
        logger.debug(f"Saved snapshot of {len(lights)} lights")

    @property
    def total_events_logged(self) -> int:
        """Get total number of events logged this session."""
        return self._event_count
