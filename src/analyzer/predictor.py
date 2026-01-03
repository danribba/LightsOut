"""Lighting predictor based on detected patterns."""

from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from src.storage.database import Database


class LightingPredictor:
    """Predicts lighting needs based on learned patterns."""

    def __init__(
        self,
        database: Database,
        min_confidence: float = 0.7,
        lookahead_minutes: int = 5,
    ):
        """
        Initialize predictor.

        Args:
            database: Database instance
            min_confidence: Minimum confidence for predictions
            lookahead_minutes: How far ahead to predict
        """
        self.db = database
        self.min_confidence = min_confidence
        self.lookahead = lookahead_minutes

    def get_predictions(self) -> list[dict]:
        """
        Get predictions for the current time.

        Returns:
            List of predicted actions to take.
        """
        now = datetime.now()
        predictions = []

        # Get active patterns
        patterns = self.db.get_active_patterns()

        for pattern in patterns:
            if pattern.confidence < self.min_confidence:
                continue

            prediction = self._check_pattern_match(pattern, now)
            if prediction:
                predictions.append(prediction)

        return predictions

    def _check_pattern_match(self, pattern, now: datetime) -> Optional[dict]:
        """Check if a pattern matches current conditions."""
        # Check weekday
        if pattern.weekdays:
            weekdays = [int(w) for w in pattern.weekdays.split(",") if w]
            if now.weekday() not in weekdays:
                return None

        # Check time window
        if pattern.time_start:
            try:
                start_hour, start_min = map(int, pattern.time_start.split(":"))
                pattern_time = now.replace(hour=start_hour, minute=start_min, second=0)

                # Check if we're within the lookahead window
                time_diff = (pattern_time - now).total_seconds() / 60

                if not (0 <= time_diff <= self.lookahead):
                    return None

            except (ValueError, AttributeError):
                pass

        return {
            "pattern_id": pattern.id,
            "pattern_type": pattern.pattern_type,
            "description": pattern.description,
            "action": pattern.action,
            "confidence": pattern.confidence,
            "trigger_time": now,
        }

    def should_trigger_sequence(
        self,
        trigger_light_id: str,
        trigger_event: str,
    ) -> list[dict]:
        """
        Check if a light event should trigger a sequence.

        Args:
            trigger_light_id: ID of the light that changed
            trigger_event: Type of event (on/off)

        Returns:
            List of actions to take.
        """
        actions = []
        patterns = self.db.get_active_patterns()

        for pattern in patterns:
            if pattern.pattern_type != "sequence":
                continue

            if pattern.confidence < self.min_confidence:
                continue

            # Parse the action JSON
            try:
                import json
                action = json.loads(pattern.action.replace("'", '"'))

                trigger = action.get("trigger", {})
                if (
                    trigger.get("light_id") == trigger_light_id
                    and trigger.get("type") == trigger_event
                ):
                    response = action.get("response", {})
                    delay = action.get("delay_seconds", 0)

                    actions.append({
                        "pattern_id": pattern.id,
                        "light_id": response.get("light_id"),
                        "action": response.get("type"),
                        "delay_seconds": delay,
                        "confidence": pattern.confidence,
                    })

            except (json.JSONDecodeError, AttributeError):
                continue

        return actions

    def get_recommendations(self) -> list[dict]:
        """
        Get lighting recommendations for the user.

        Returns:
            List of recommendations (not auto-executed).
        """
        predictions = self.get_predictions()
        recommendations = []

        for pred in predictions:
            if pred["confidence"] >= 0.8:
                recommendations.append({
                    "type": "suggestion",
                    "message": f"Baserat på dina vanor: {pred['description']}",
                    "confidence": pred["confidence"],
                    "action": pred["action"],
                })

        return recommendations

    def update_pattern_from_feedback(
        self,
        pattern_id: int,
        was_correct: bool,
    ):
        """
        Update pattern confidence based on user feedback.

        Args:
            pattern_id: ID of the pattern
            was_correct: Whether the prediction was correct
        """
        with self.db.get_session() as session:
            from src.storage.database import DetectedPattern

            pattern = session.query(DetectedPattern).get(pattern_id)
            if pattern:
                # Adjust confidence
                if was_correct:
                    pattern.confidence = min(1.0, pattern.confidence + 0.05)
                else:
                    pattern.confidence = max(0.0, pattern.confidence - 0.1)

                # Deactivate if confidence drops too low
                if pattern.confidence < 0.3:
                    pattern.is_active = False
                    logger.info(f"Deactivated low-confidence pattern: {pattern.description}")

                session.commit()
