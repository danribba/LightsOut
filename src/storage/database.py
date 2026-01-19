"""SQLite database for storing light events and patterns."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()


class LightEventRecord(Base):
    """Database model for light events."""

    __tablename__ = "light_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    light_id = Column(String(50), nullable=False, index=True)
    light_name = Column(String(100))
    timestamp = Column(DateTime, nullable=False, index=True)
    event_type = Column(String(50), nullable=False)  # on, off, brightness, color
    old_value = Column(String(100))
    new_value = Column(String(100))

    # Time context for pattern analysis
    weekday = Column(Integer)  # 0=Monday, 6=Sunday
    hour = Column(Integer)
    minute = Column(Integer)

    def __repr__(self):
        return f"<LightEvent {self.light_name} {self.event_type} @ {self.timestamp}>"


class LightStateSnapshot(Base):
    """Database model for periodic state snapshots."""

    __tablename__ = "light_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    light_id = Column(String(50), nullable=False, index=True)
    light_name = Column(String(100))
    is_on = Column(Boolean)
    brightness = Column(Integer)
    hue = Column(Integer)
    saturation = Column(Integer)
    color_temp = Column(Integer)


class DetectedPattern(Base):
    """Database model for detected patterns."""

    __tablename__ = "detected_patterns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern_type = Column(String(50))  # time_based, sequence, correlation
    description = Column(String(500))
    light_ids = Column(String(200))  # Comma-separated light IDs
    weekdays = Column(String(20))  # Comma-separated weekdays (0-6)
    time_start = Column(String(10))  # HH:MM format
    time_end = Column(String(10))
    action = Column(String(200))  # JSON-encoded action
    confidence = Column(Float)
    occurrence_count = Column(Integer)
    last_seen = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)


class Automation(Base):
    """Database model for user-defined automations."""

    __tablename__ = "automations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500))

    # Trigger configuration
    trigger_type = Column(String(50))  # "time", "event", "sunrise", "sunset"
    trigger_config = Column(Text)  # JSON with trigger-specific settings

    # Target configuration
    target_type = Column(String(20))  # "light", "room", "scene"
    target_ids = Column(String(200))  # Comma-separated IDs

    # Action configuration (JSON with all Hue params)
    action_config = Column(Text)

    # Status and metadata
    is_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    last_triggered = Column(DateTime)
    trigger_count = Column(Integer, default=0)

    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "trigger_type": self.trigger_type,
            "trigger_config": json.loads(self.trigger_config) if self.trigger_config else {},
            "target_type": self.target_type,
            "target_ids": self.target_ids.split(",") if self.target_ids else [],
            "action_config": json.loads(self.action_config) if self.action_config else {},
            "is_enabled": self.is_enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_triggered": self.last_triggered.isoformat() if self.last_triggered else None,
            "trigger_count": self.trigger_count,
        }


class Database:
    """Handles all database operations."""

    def __init__(self, db_path: str = "data/hue_events.db"):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        self.SessionLocal = sessionmaker(bind=self.engine)

        # Create tables
        Base.metadata.create_all(self.engine)
        logger.info(f"Database initialized at {self.db_path}")

    def get_session(self) -> Session:
        """Get a new database session."""
        return self.SessionLocal()

    def add_event(
        self,
        light_id: str,
        light_name: str,
        event_type: str,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> LightEventRecord:
        """
        Record a light event.

        Args:
            light_id: ID of the light
            light_name: Name of the light
            event_type: Type of event (on, off, brightness, color)
            old_value: Previous value
            new_value: New value
            timestamp: Event time (defaults to now)

        Returns:
            The created event record.
        """
        ts = timestamp or datetime.now()

        event = LightEventRecord(
            light_id=light_id,
            light_name=light_name,
            timestamp=ts,
            event_type=event_type,
            old_value=old_value,
            new_value=new_value,
            weekday=ts.weekday(),
            hour=ts.hour,
            minute=ts.minute,
        )

        with self.get_session() as session:
            session.add(event)
            session.commit()
            session.refresh(event)
            logger.debug(f"Recorded event: {event}")
            return event

    def add_snapshot(self, light_state: dict) -> LightStateSnapshot:
        """
        Save a snapshot of light state.

        Args:
            light_state: Dictionary with light state data.

        Returns:
            The created snapshot record.
        """
        snapshot = LightStateSnapshot(
            timestamp=datetime.now(),
            light_id=light_state["light_id"],
            light_name=light_state.get("name"),
            is_on=light_state.get("is_on"),
            brightness=light_state.get("brightness"),
            hue=light_state.get("hue"),
            saturation=light_state.get("saturation"),
            color_temp=light_state.get("color_temp"),
        )

        with self.get_session() as session:
            session.add(snapshot)
            session.commit()
            return snapshot

    def get_events(
        self,
        light_id: Optional[str] = None,
        event_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[LightEventRecord]:
        """
        Query light events with filters.

        Args:
            light_id: Filter by light ID
            event_type: Filter by event type
            start_date: Start of date range
            end_date: End of date range
            limit: Maximum number of results

        Returns:
            List of matching event records.
        """
        with self.get_session() as session:
            query = session.query(LightEventRecord)

            if light_id:
                query = query.filter(LightEventRecord.light_id == light_id)
            if event_type:
                query = query.filter(LightEventRecord.event_type == event_type)
            if start_date:
                query = query.filter(LightEventRecord.timestamp >= start_date)
            if end_date:
                query = query.filter(LightEventRecord.timestamp <= end_date)

            return query.order_by(LightEventRecord.timestamp.desc()).limit(limit).all()

    def get_events_by_time_window(
        self,
        weekday: int,
        hour: int,
        minute_range: int = 15,
        days_back: int = 30,
    ) -> list[LightEventRecord]:
        """
        Get events that occurred at similar times on similar days.

        Args:
            weekday: Day of week (0=Monday)
            hour: Hour of day (0-23)
            minute_range: Minutes +/- to search
            days_back: How many days of history to search

        Returns:
            List of matching events.
        """
        start_date = datetime.now() - timedelta(days=days_back)

        with self.get_session() as session:
            return (
                session.query(LightEventRecord)
                .filter(LightEventRecord.timestamp >= start_date)
                .filter(LightEventRecord.weekday == weekday)
                .filter(LightEventRecord.hour == hour)
                .all()
            )

    def save_pattern(self, pattern: dict) -> DetectedPattern:
        """
        Save a detected pattern.

        Args:
            pattern: Pattern data dictionary.

        Returns:
            The created pattern record.
        """
        db_pattern = DetectedPattern(
            pattern_type=pattern.get("type"),
            description=pattern.get("description"),
            light_ids=",".join(pattern.get("light_ids", [])),
            weekdays=",".join(map(str, pattern.get("weekdays", []))),
            time_start=pattern.get("time_start"),
            time_end=pattern.get("time_end"),
            action=str(pattern.get("action")),
            confidence=pattern.get("confidence", 0.0),
            occurrence_count=pattern.get("occurrences", 0),
            last_seen=datetime.now(),
        )

        with self.get_session() as session:
            session.add(db_pattern)
            session.commit()
            logger.info(f"Saved pattern: {db_pattern.description}")
            return db_pattern

    def get_active_patterns(self) -> list[DetectedPattern]:
        """Get all active patterns."""
        with self.get_session() as session:
            return (
                session.query(DetectedPattern)
                .filter(DetectedPattern.is_active == True)
                .all()
            )

    def cleanup_old_events(self, days: int = 90):
        """
        Remove events older than specified days.

        Args:
            days: Delete events older than this many days.
        """
        cutoff = datetime.now() - timedelta(days=days)

        with self.get_session() as session:
            deleted = (
                session.query(LightEventRecord)
                .filter(LightEventRecord.timestamp < cutoff)
                .delete()
            )
            session.commit()
            if deleted:
                logger.info(f"Cleaned up {deleted} old events")

    def get_statistics(self) -> dict:
        """Get database statistics."""
        with self.get_session() as session:
            total_events = session.query(LightEventRecord).count()
            total_patterns = session.query(DetectedPattern).count()
            active_patterns = (
                session.query(DetectedPattern)
                .filter(DetectedPattern.is_active == True)
                .count()
            )

            # Get date range
            oldest = (
                session.query(LightEventRecord)
                .order_by(LightEventRecord.timestamp.asc())
                .first()
            )
            newest = (
                session.query(LightEventRecord)
                .order_by(LightEventRecord.timestamp.desc())
                .first()
            )

            return {
                "total_events": total_events,
                "total_patterns": total_patterns,
                "active_patterns": active_patterns,
                "oldest_event": oldest.timestamp if oldest else None,
                "newest_event": newest.timestamp if newest else None,
                "database_path": str(self.db_path),
            }

    # ===== Automation CRUD methods =====

    def get_all_automations(self) -> list[Automation]:
        """Get all automations."""
        with self.get_session() as session:
            return session.query(Automation).order_by(Automation.created_at.desc()).all()

    def get_automation(self, automation_id: int) -> Optional[Automation]:
        """Get a single automation by ID."""
        with self.get_session() as session:
            return session.query(Automation).filter(Automation.id == automation_id).first()

    def get_enabled_automations(self) -> list[Automation]:
        """Get all enabled automations."""
        with self.get_session() as session:
            return (
                session.query(Automation)
                .filter(Automation.is_enabled == True)
                .all()
            )

    def create_automation(
        self,
        name: str,
        trigger_type: str,
        trigger_config: dict,
        target_type: str,
        target_ids: list[str],
        action_config: dict,
        description: str = "",
    ) -> Automation:
        """
        Create a new automation.

        Args:
            name: Name of the automation
            trigger_type: Type of trigger (time, event, sunrise, sunset)
            trigger_config: Trigger-specific configuration
            target_type: Type of target (light, room, scene)
            target_ids: List of target IDs
            action_config: Hue action parameters
            description: Optional description

        Returns:
            The created automation.
        """
        automation = Automation(
            name=name,
            description=description,
            trigger_type=trigger_type,
            trigger_config=json.dumps(trigger_config),
            target_type=target_type,
            target_ids=",".join(target_ids),
            action_config=json.dumps(action_config),
        )

        with self.get_session() as session:
            session.add(automation)
            session.commit()
            session.refresh(automation)
            logger.info(f"Created automation: {automation.name}")
            return automation

    def update_automation(
        self,
        automation_id: int,
        **kwargs,
    ) -> Optional[Automation]:
        """
        Update an existing automation.

        Args:
            automation_id: ID of automation to update
            **kwargs: Fields to update

        Returns:
            Updated automation or None if not found.
        """
        with self.get_session() as session:
            automation = session.query(Automation).filter(Automation.id == automation_id).first()
            if not automation:
                return None

            # Handle special fields
            if "trigger_config" in kwargs and isinstance(kwargs["trigger_config"], dict):
                kwargs["trigger_config"] = json.dumps(kwargs["trigger_config"])
            if "action_config" in kwargs and isinstance(kwargs["action_config"], dict):
                kwargs["action_config"] = json.dumps(kwargs["action_config"])
            if "target_ids" in kwargs and isinstance(kwargs["target_ids"], list):
                kwargs["target_ids"] = ",".join(kwargs["target_ids"])

            for key, value in kwargs.items():
                if hasattr(automation, key):
                    setattr(automation, key, value)

            automation.updated_at = datetime.now()
            session.commit()
            session.refresh(automation)
            logger.info(f"Updated automation: {automation.name}")
            return automation

    def delete_automation(self, automation_id: int) -> bool:
        """
        Delete an automation.

        Args:
            automation_id: ID of automation to delete

        Returns:
            True if deleted, False if not found.
        """
        with self.get_session() as session:
            automation = session.query(Automation).filter(Automation.id == automation_id).first()
            if not automation:
                return False

            session.delete(automation)
            session.commit()
            logger.info(f"Deleted automation: {automation.name}")
            return True

    def toggle_automation(self, automation_id: int) -> Optional[Automation]:
        """
        Toggle automation enabled/disabled status.

        Args:
            automation_id: ID of automation to toggle

        Returns:
            Updated automation or None if not found.
        """
        with self.get_session() as session:
            automation = session.query(Automation).filter(Automation.id == automation_id).first()
            if not automation:
                return None

            automation.is_enabled = not automation.is_enabled
            automation.updated_at = datetime.now()
            session.commit()
            session.refresh(automation)
            logger.info(f"Toggled automation {automation.name}: {'enabled' if automation.is_enabled else 'disabled'}")
            return automation

    def record_automation_trigger(self, automation_id: int):
        """Record that an automation was triggered."""
        with self.get_session() as session:
            automation = session.query(Automation).filter(Automation.id == automation_id).first()
            if automation:
                automation.last_triggered = datetime.now()
                automation.trigger_count = (automation.trigger_count or 0) + 1
                session.commit()
