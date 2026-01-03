"""Data models for Hue light states and events."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class LightState:
    """Represents the current state of a Hue light."""

    light_id: str
    name: str
    is_on: bool
    brightness: int  # 0-254
    hue: Optional[int] = None  # 0-65535
    saturation: Optional[int] = None  # 0-254
    color_temp: Optional[int] = None  # 153-500 mirek
    reachable: bool = True

    @property
    def brightness_percent(self) -> float:
        """Get brightness as percentage (0-100)."""
        return round((self.brightness / 254) * 100, 1)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "light_id": self.light_id,
            "name": self.name,
            "is_on": self.is_on,
            "brightness": self.brightness,
            "hue": self.hue,
            "saturation": self.saturation,
            "color_temp": self.color_temp,
            "reachable": self.reachable,
        }

    @classmethod
    def from_hue_api(cls, light_id: str, data: dict) -> "LightState":
        """Create LightState from Hue API response."""
        state = data.get("state", {})
        return cls(
            light_id=light_id,
            name=data.get("name", f"Light {light_id}"),
            is_on=state.get("on", False),
            brightness=state.get("bri", 0),
            hue=state.get("hue"),
            saturation=state.get("sat"),
            color_temp=state.get("ct"),
            reachable=state.get("reachable", True),
        )


@dataclass
class LightEvent:
    """Represents a change in light state."""

    light_id: str
    light_name: str
    timestamp: datetime
    event_type: str  # "on", "off", "brightness", "color", "scene"
    old_value: Optional[str] = None
    new_value: Optional[str] = None

    # Context information
    weekday: int = field(default_factory=lambda: datetime.now().weekday())
    hour: int = field(default_factory=lambda: datetime.now().hour)
    minute: int = field(default_factory=lambda: datetime.now().minute)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "light_id": self.light_id,
            "light_name": self.light_name,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "weekday": self.weekday,
            "hour": self.hour,
            "minute": self.minute,
        }


@dataclass
class Room:
    """Represents a Hue room/zone."""

    room_id: str
    name: str
    light_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_hue_api(cls, room_id: str, data: dict) -> "Room":
        """Create Room from Hue API response."""
        return cls(
            room_id=room_id,
            name=data.get("name", f"Room {room_id}"),
            light_ids=data.get("lights", []),
        )
