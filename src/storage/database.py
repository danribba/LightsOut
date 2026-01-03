"""SQLite database for storing light events and patterns."""

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
