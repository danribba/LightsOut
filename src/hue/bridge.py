"""Philips Hue Bridge communication."""

import os
import time
from typing import Optional

from loguru import logger

try:
    from phue import Bridge, PhueRegistrationException
except ImportError:
    Bridge = None
    PhueRegistrationException = Exception

from .models import LightState, Room


class HueBridge:
    """Handles communication with Philips Hue Bridge."""

    def __init__(self, ip_address: Optional[str] = None):
        """
        Initialize Hue Bridge connection.

        Args:
            ip_address: Bridge IP. If None, will try to auto-discover.
        """
        self.ip_address = ip_address or os.getenv("HUE_BRIDGE_IP")
        self._bridge: Optional[Bridge] = None
        self._previous_states: dict[str, LightState] = {}

    def connect(self) -> bool:
        """
        Connect to the Hue Bridge.

        Returns:
            True if connection successful, False otherwise.
        """
        if Bridge is None:
            logger.error("phue library not installed. Run: pip install phue")
            return False

        if not self.ip_address:
            logger.info("No IP address provided, attempting auto-discovery...")
            self.ip_address = self._discover_bridge()

        if not self.ip_address:
            logger.error("Could not find Hue Bridge. Please specify IP address.")
            return False

        try:
            logger.info(f"Connecting to Hue Bridge at {self.ip_address}")
            self._bridge = Bridge(self.ip_address)
            self._bridge.connect()
            logger.success(f"Connected to Hue Bridge: {self.ip_address}")
            return True

        except PhueRegistrationException:
            logger.warning("Press the link button on your Hue Bridge, then retry...")
            self._wait_for_button_press()
            return self.connect()

        except Exception as e:
            logger.error(f"Failed to connect to Hue Bridge: {e}")
            return False

    def _discover_bridge(self) -> Optional[str]:
        """Attempt to auto-discover Hue Bridge on network."""
        try:
            import requests

            response = requests.get(
                "https://discovery.meethue.com", timeout=10
            )
            bridges = response.json()
            if bridges:
                ip = bridges[0].get("internalipaddress")
                logger.info(f"Discovered Hue Bridge at {ip}")
                return ip
        except Exception as e:
            logger.warning(f"Auto-discovery failed: {e}")
        return None

    def _wait_for_button_press(self, timeout: int = 30):
        """Wait for user to press the link button."""
        logger.info(f"Waiting {timeout} seconds for button press...")
        for i in range(timeout):
            print(f"\rWaiting... {timeout - i}s remaining", end="", flush=True)
            time.sleep(1)
        print()

    @property
    def is_connected(self) -> bool:
        """Check if bridge is connected."""
        return self._bridge is not None

    def get_all_lights(self) -> dict[str, LightState]:
        """
        Get current state of all lights.

        Returns:
            Dictionary mapping light_id to LightState.
        """
        if not self._bridge:
            logger.error("Not connected to bridge")
            return {}

        lights = {}
        try:
            api_lights = self._bridge.get_light_objects("id")
            for light_id, light in api_lights.items():
                light_data = self._bridge.get_light(light_id)
                lights[str(light_id)] = LightState.from_hue_api(
                    str(light_id), light_data
                )
        except Exception as e:
            logger.error(f"Failed to get lights: {e}")

        return lights

    def get_all_rooms(self) -> dict[str, Room]:
        """
        Get all rooms/groups from bridge.

        Returns:
            Dictionary mapping room_id to Room.
        """
        if not self._bridge:
            logger.error("Not connected to bridge")
            return {}

        rooms = {}
        try:
            groups = self._bridge.get_group()
            for group_id, group_data in groups.items():
                if group_data.get("type") == "Room":
                    rooms[str(group_id)] = Room.from_hue_api(str(group_id), group_data)
        except Exception as e:
            logger.error(f"Failed to get rooms: {e}")

        return rooms

    def detect_changes(self) -> list[tuple[LightState, LightState]]:
        """
        Detect changes in light states since last check.

        Returns:
            List of (old_state, new_state) tuples for changed lights.
        """
        changes = []
        current_states = self.get_all_lights()

        for light_id, new_state in current_states.items():
            old_state = self._previous_states.get(light_id)

            if old_state is None:
                # First time seeing this light
                self._previous_states[light_id] = new_state
                continue

            # Check for changes
            if self._has_changed(old_state, new_state):
                changes.append((old_state, new_state))
                self._previous_states[light_id] = new_state

        return changes

    def _has_changed(self, old: LightState, new: LightState) -> bool:
        """Check if light state has meaningfully changed."""
        if old.is_on != new.is_on:
            return True
        if old.is_on and new.is_on:
            # Only check other attributes if light is on
            if abs(old.brightness - new.brightness) > 5:
                return True
            if old.hue is not None and new.hue is not None:
                if abs(old.hue - new.hue) > 1000:
                    return True
            if old.color_temp is not None and new.color_temp is not None:
                if abs(old.color_temp - new.color_temp) > 10:
                    return True
        return False

    def set_light_state(
        self,
        light_id: str,
        on: Optional[bool] = None,
        brightness: Optional[int] = None,
        hue: Optional[int] = None,
        saturation: Optional[int] = None,
        color_temp: Optional[int] = None,
        transition_time: Optional[int] = None,
        alert: Optional[str] = None,
        effect: Optional[str] = None,
        xy: Optional[list[float]] = None,
    ) -> bool:
        """
        Set light state with advanced Hue features.

        Args:
            light_id: ID of the light to control
            on: Turn on/off
            brightness: Brightness level (0-254)
            hue: Color hue (0-65535)
            saturation: Color saturation (0-254)
            color_temp: Color temperature in mirek (153-500)
            transition_time: Fade time in 1/10 seconds (e.g., 6000 = 10 min)
            alert: Alert effect ("none", "select", "lselect")
            effect: Light effect ("none", "colorloop")
            xy: CIE color coordinates [x, y]

        Returns:
            True if successful, False otherwise.
        """
        if not self._bridge:
            logger.error("Not connected to bridge")
            return False

        try:
            command = {}
            if on is not None:
                command["on"] = on
            if brightness is not None:
                command["bri"] = max(0, min(254, brightness))
            if hue is not None:
                command["hue"] = max(0, min(65535, hue))
            if saturation is not None:
                command["sat"] = max(0, min(254, saturation))
            if color_temp is not None:
                command["ct"] = max(153, min(500, color_temp))
            if transition_time is not None:
                command["transitiontime"] = max(0, min(65535, transition_time))
            if alert is not None and alert in ("none", "select", "lselect"):
                command["alert"] = alert
            if effect is not None and effect in ("none", "colorloop"):
                command["effect"] = effect
            if xy is not None and len(xy) == 2:
                command["xy"] = [max(0, min(1, xy[0])), max(0, min(1, xy[1]))]

            if command:
                self._bridge.set_light(int(light_id), command)
                logger.debug(f"Set light {light_id}: {command}")
                return True

        except Exception as e:
            logger.error(f"Failed to set light {light_id}: {e}")

        return False

    def set_group_state(
        self,
        group_id: str,
        on: Optional[bool] = None,
        brightness: Optional[int] = None,
        hue: Optional[int] = None,
        saturation: Optional[int] = None,
        color_temp: Optional[int] = None,
        transition_time: Optional[int] = None,
        alert: Optional[str] = None,
        effect: Optional[str] = None,
        xy: Optional[list[float]] = None,
        scene: Optional[str] = None,
    ) -> bool:
        """
        Set group/room state with advanced Hue features.

        Args:
            group_id: ID of the group/room to control
            on: Turn on/off
            brightness: Brightness level (0-254)
            hue: Color hue (0-65535)
            saturation: Color saturation (0-254)
            color_temp: Color temperature in mirek (153-500)
            transition_time: Fade time in 1/10 seconds
            alert: Alert effect ("none", "select", "lselect")
            effect: Light effect ("none", "colorloop")
            xy: CIE color coordinates [x, y]
            scene: Scene ID to activate

        Returns:
            True if successful, False otherwise.
        """
        if not self._bridge:
            logger.error("Not connected to bridge")
            return False

        try:
            command = {}
            if on is not None:
                command["on"] = on
            if brightness is not None:
                command["bri"] = max(0, min(254, brightness))
            if hue is not None:
                command["hue"] = max(0, min(65535, hue))
            if saturation is not None:
                command["sat"] = max(0, min(254, saturation))
            if color_temp is not None:
                command["ct"] = max(153, min(500, color_temp))
            if transition_time is not None:
                command["transitiontime"] = max(0, min(65535, transition_time))
            if alert is not None and alert in ("none", "select", "lselect"):
                command["alert"] = alert
            if effect is not None and effect in ("none", "colorloop"):
                command["effect"] = effect
            if xy is not None and len(xy) == 2:
                command["xy"] = [max(0, min(1, xy[0])), max(0, min(1, xy[1]))]
            if scene is not None:
                command["scene"] = scene

            if command:
                self._bridge.set_group(int(group_id), command)
                logger.debug(f"Set group {group_id}: {command}")
                return True

        except Exception as e:
            logger.error(f"Failed to set group {group_id}: {e}")

        return False

    def get_scenes(self) -> dict:
        """Get all scenes from bridge."""
        if not self._bridge:
            logger.error("Not connected to bridge")
            return {}

        try:
            return self._bridge.get_scene() or {}
        except Exception as e:
            logger.error(f"Failed to get scenes: {e}")
            return {}
