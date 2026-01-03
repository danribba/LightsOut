"""Pattern detection for light usage analysis."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from loguru import logger

from src.storage.database import Database, LightEventRecord


class PatternDetector:
    """Detects patterns in light usage data."""

    def __init__(
        self,
        database: Database,
        min_occurrences: int = 3,
        time_window_minutes: int = 15,
        confidence_threshold: float = 0.7,
    ):
        """
        Initialize pattern detector.

        Args:
            database: Database instance
            min_occurrences: Minimum times a pattern must occur to be valid
            time_window_minutes: Time window for grouping similar events
            confidence_threshold: Minimum confidence to report a pattern
        """
        self.db = database
        self.min_occurrences = min_occurrences
        self.time_window = time_window_minutes
        self.confidence_threshold = confidence_threshold

    def analyze(self, days_back: int = 30) -> list[dict]:
        """
        Analyze light events and detect patterns.

        Args:
            days_back: Number of days of history to analyze

        Returns:
            List of detected patterns.
        """
        logger.info(f"Analyzing {days_back} days of light data...")

        start_date = datetime.now() - timedelta(days=days_back)
        events = self.db.get_events(start_date=start_date, limit=10000)

        if not events:
            logger.warning("No events found to analyze")
            return []

        # Convert to DataFrame for easier analysis
        df = self._events_to_dataframe(events)

        patterns = []

        # Detect different pattern types
        patterns.extend(self._detect_time_patterns(df))
        patterns.extend(self._detect_sequence_patterns(df))
        patterns.extend(self._detect_correlation_patterns(df))

        # Filter by confidence
        patterns = [p for p in patterns if p["confidence"] >= self.confidence_threshold]

        logger.info(f"Detected {len(patterns)} patterns")
        return patterns

    def _events_to_dataframe(self, events: list[LightEventRecord]) -> pd.DataFrame:
        """Convert event records to pandas DataFrame."""
        data = []
        for e in events:
            data.append({
                "light_id": e.light_id,
                "light_name": e.light_name,
                "timestamp": e.timestamp,
                "event_type": e.event_type,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "weekday": e.weekday,
                "hour": e.hour,
                "minute": e.minute,
            })
        return pd.DataFrame(data)

    def _detect_time_patterns(self, df: pd.DataFrame) -> list[dict]:
        """
        Detect time-based patterns.

        Example: "Kitchen light turns on at 07:00 on weekdays"
        """
        patterns = []

        # Group by light, weekday, hour, and event type
        grouped = df.groupby(["light_id", "light_name", "weekday", "hour", "event_type"])

        for (light_id, light_name, weekday, hour, event_type), group in grouped:
            occurrences = len(group)

            if occurrences >= self.min_occurrences:
                # Calculate confidence based on consistency
                total_days_in_period = len(df["timestamp"].dt.date.unique())
                expected_occurrences = total_days_in_period / 7  # Expected for this weekday
                confidence = min(1.0, occurrences / max(expected_occurrences, 1))

                weekday_names = ["Mån", "Tis", "Ons", "Tor", "Fre", "Lör", "Sön"]
                action_text = "tänds" if event_type == "on" else "släcks"

                pattern = {
                    "type": "time_based",
                    "description": (
                        f"{light_name} {action_text} "
                        f"kl {hour:02d}:00 på {weekday_names[weekday]}ar"
                    ),
                    "light_ids": [light_id],
                    "weekdays": [weekday],
                    "time_start": f"{hour:02d}:00",
                    "time_end": f"{hour:02d}:59",
                    "action": {"type": event_type, "light_id": light_id},
                    "confidence": round(confidence, 2),
                    "occurrences": occurrences,
                }
                patterns.append(pattern)

        return patterns

    def _detect_sequence_patterns(self, df: pd.DataFrame) -> list[dict]:
        """
        Detect sequential patterns.

        Example: "When hall light turns on, kitchen light turns on within 2 minutes"
        """
        patterns = []

        # Sort by timestamp
        df_sorted = df.sort_values("timestamp")

        # Look for events that commonly follow each other
        sequence_counts = defaultdict(int)
        sequence_details = defaultdict(list)

        for i in range(len(df_sorted) - 1):
            current = df_sorted.iloc[i]
            next_event = df_sorted.iloc[i + 1]

            # Check if next event is within time window
            time_diff = (next_event["timestamp"] - current["timestamp"]).total_seconds()

            if 0 < time_diff <= self.time_window * 60:
                # Different lights
                if current["light_id"] != next_event["light_id"]:
                    key = (
                        current["light_id"],
                        current["light_name"],
                        current["event_type"],
                        next_event["light_id"],
                        next_event["light_name"],
                        next_event["event_type"],
                    )
                    sequence_counts[key] += 1
                    sequence_details[key].append(time_diff)

        # Convert to patterns
        for key, count in sequence_counts.items():
            if count >= self.min_occurrences:
                (
                    light1_id, light1_name, event1_type,
                    light2_id, light2_name, event2_type,
                ) = key

                avg_delay = sum(sequence_details[key]) / len(sequence_details[key])
                confidence = min(1.0, count / (self.min_occurrences * 2))

                action1 = "tänds" if event1_type == "on" else "släcks"
                action2 = "tänds" if event2_type == "on" else "släcks"

                pattern = {
                    "type": "sequence",
                    "description": (
                        f"När {light1_name} {action1}, "
                        f"{action2} {light2_name} inom {int(avg_delay)}s"
                    ),
                    "light_ids": [light1_id, light2_id],
                    "weekdays": list(range(7)),  # All days
                    "time_start": None,
                    "time_end": None,
                    "action": {
                        "trigger": {"light_id": light1_id, "type": event1_type},
                        "response": {"light_id": light2_id, "type": event2_type},
                        "delay_seconds": int(avg_delay),
                    },
                    "confidence": round(confidence, 2),
                    "occurrences": count,
                }
                patterns.append(pattern)

        return patterns

    def _detect_correlation_patterns(self, df: pd.DataFrame) -> list[dict]:
        """
        Detect correlated patterns.

        Example: "Living room and dining room lights are usually on together"
        """
        patterns = []

        # This requires snapshot data for correlation analysis
        # For now, we'll detect lights that commonly change state together

        df_sorted = df.sort_values("timestamp")

        # Find lights that change within seconds of each other
        correlation_counts = defaultdict(int)

        for i in range(len(df_sorted) - 1):
            current = df_sorted.iloc[i]

            # Look at next few events within 5 seconds
            for j in range(i + 1, min(i + 5, len(df_sorted))):
                next_event = df_sorted.iloc[j]
                time_diff = (next_event["timestamp"] - current["timestamp"]).total_seconds()

                if time_diff > 5:
                    break

                if (
                    current["light_id"] != next_event["light_id"]
                    and current["event_type"] == next_event["event_type"]
                ):
                    # Same action on different lights within 5 seconds
                    key = tuple(sorted([
                        (current["light_id"], current["light_name"]),
                        (next_event["light_id"], next_event["light_name"]),
                    ]))
                    correlation_counts[(key, current["event_type"])] += 1

        for (lights, event_type), count in correlation_counts.items():
            if count >= self.min_occurrences:
                light1, light2 = lights
                confidence = min(1.0, count / (self.min_occurrences * 3))
                action = "tänds" if event_type == "on" else "släcks"

                pattern = {
                    "type": "correlation",
                    "description": (
                        f"{light1[1]} och {light2[1]} {action} "
                        f"ofta tillsammans"
                    ),
                    "light_ids": [light1[0], light2[0]],
                    "weekdays": list(range(7)),
                    "time_start": None,
                    "time_end": None,
                    "action": {
                        "type": "group",
                        "event_type": event_type,
                        "lights": [light1[0], light2[0]],
                    },
                    "confidence": round(confidence, 2),
                    "occurrences": count,
                }
                patterns.append(pattern)

        return patterns

    def get_pattern_summary(self, patterns: list[dict]) -> str:
        """Generate a human-readable summary of detected patterns."""
        if not patterns:
            return "Inga mönster upptäckta ännu. Samla mer data."

        summary_lines = ["📊 Upptäckta mönster:\n"]

        # Group by type
        by_type = defaultdict(list)
        for p in patterns:
            by_type[p["type"]].append(p)

        type_names = {
            "time_based": "⏰ Tidsbaserade",
            "sequence": "🔗 Sekvenser",
            "correlation": "🔄 Korrelationer",
        }

        for pattern_type, type_patterns in by_type.items():
            summary_lines.append(f"\n{type_names.get(pattern_type, pattern_type)}:")
            for p in sorted(type_patterns, key=lambda x: -x["confidence"])[:5]:
                summary_lines.append(
                    f"  • {p['description']} "
                    f"(konfidens: {p['confidence']*100:.0f}%, "
                    f"sett {p['occurrences']} gånger)"
                )

        return "\n".join(summary_lines)
