"""titiler.openeo.services SQLAlchemy Tile Store."""

import random
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import Session, sessionmaker

from .base import (
    NoTileAvailableError,
    TileAlreadyLockedError,
    TileAssignmentStore,
    TileNotAssignedError,
)
from .sqlalchemy import Base


class TileAssignment(Base):
    """SQLAlchemy Tile Assignment Model."""

    __tablename__ = "tile_assignments"

    id = Column(Integer, primary_key=True)
    service_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    x = Column(Integer, nullable=False)
    y = Column(Integer, nullable=False)
    z = Column(Integer, nullable=False)
    stage = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("service_id", "x", "y", "z", name="unique_tile"),
    )


class SQLAlchemyTileStore(TileAssignmentStore):
    """SQLAlchemy implementation of TileAssignmentStore."""

    def __init__(self, store: str):
        """Initialize the store.

        Args:
            store: SQLAlchemy connection string
        """
        super().__init__(store)
        # Create engine and session factory
        self._engine = create_engine(store)
        self._session_factory = sessionmaker(bind=self._engine)
        # Ensure tile_assignments table exists
        Base.metadata.create_all(self._engine)

    def get_user_tile(self, service_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user's currently assigned tile."""
        with Session(self._engine) as session:
            result = session.execute(
                select(TileAssignment).where(
                    TileAssignment.service_id == service_id,
                    TileAssignment.user_id == user_id,
                )
            ).scalar_one_or_none()

            if not result:
                return None

            return {
                "service_id": result.service_id,
                "x": result.x,
                "y": result.y,
                "z": result.z,
                "stage": result.stage,
                "user_id": result.user_id,
            }

    def claim_tile(
        self,
        service_id: str,
        user_id: str,
        zoom: int,
        x_range: Tuple[int, int],
        y_range: Tuple[int, int],
    ) -> Dict[str, Any]:
        """Claim a tile for a user within given ranges."""
        # First check if user already has a tile
        existing_tile = self.get_user_tile(service_id, user_id)
        if existing_tile:
            return existing_tile

        with Session(self._engine) as session:
            # Get all assigned tiles within the ranges
            assigned_tiles = (
                session.execute(
                    select(TileAssignment).where(
                        TileAssignment.service_id == service_id,
                        TileAssignment.z == zoom,
                        TileAssignment.x >= x_range[0],
                        TileAssignment.x <= x_range[1],
                        TileAssignment.y >= y_range[0],
                        TileAssignment.y <= y_range[1],
                    )
                )
                .scalars()
                .all()
            )

            # Create set of assigned coordinates
            assigned_coords = {(tile.x, tile.y) for tile in assigned_tiles}

            # Generate all possible coordinates within ranges
            all_coords = [
                (x, y)
                for x in range(x_range[0], x_range[1] + 1)
                for y in range(y_range[0], y_range[1] + 1)
            ]

            # Get available coordinates
            available_coords = [
                coord for coord in all_coords if coord not in assigned_coords
            ]

            if not available_coords:
                raise NoTileAvailableError(service_id, user_id)

            # Randomly select a coordinate
            x, y = random.choice(available_coords)

            # Create new tile assignment
            new_tile = TileAssignment(
                service_id=service_id,
                user_id=user_id,
                x=x,
                y=y,
                z=zoom,
                stage="claimed",
            )
            session.add(new_tile)
            session.commit()

            return {
                "service_id": service_id,
                "x": x,
                "y": y,
                "z": zoom,
                "stage": "claimed",
                "user_id": user_id,
            }

    def release_tile(
        self,
        service_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Release a user's assigned tile."""
        with Session(self._engine) as session:
            tile = session.execute(
                select(TileAssignment).where(
                    TileAssignment.service_id == service_id,
                    TileAssignment.user_id == user_id,
                )
            ).scalar_one_or_none()

            if not tile:
                raise TileNotAssignedError(
                    service_id=service_id,
                    user_id=user_id,
                )

            if tile.stage == "submitted":
                raise TileAlreadyLockedError(
                    tile.x, tile.y, tile.z, service_id, user_id
                )

            # Store tile info before deletion
            tile_info = {
                "x": tile.x,
                "y": tile.y,
                "z": tile.z,
                "stage": "released",
                "service_id": service_id,
                "user_id": user_id,
            }

            # Delete the tile assignment
            session.delete(tile)
            session.commit()

            return tile_info

    def submit_tile(
        self,
        service_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Mark a tile as submitted."""
        with Session(self._engine) as session:
            tile = session.execute(
                select(TileAssignment).where(
                    TileAssignment.service_id == service_id,
                    TileAssignment.user_id == user_id,
                )
            ).scalar_one_or_none()

            if not tile:
                raise TileNotAssignedError(
                    service_id=service_id,
                    user_id=user_id,
                )

            if tile.stage == "submitted":
                raise TileAlreadyLockedError(
                    tile.x, tile.y, tile.z, service_id, user_id
                )

            # Update tile stage
            tile.stage = "submitted"
            session.commit()

            return {
                "service_id": service_id,
                "x": tile.x,
                "y": tile.y,
                "z": tile.z,
                "stage": "submitted",
                "user_id": user_id,
            }

    def force_release_tile(
        self,
        service_id: str,
        x: int,
        y: int,
        z: int,
    ) -> Dict[str, Any]:
        """Force release a tile regardless of its state."""
        with Session(self._engine) as session:
            tile = session.execute(
                select(TileAssignment).where(
                    TileAssignment.service_id == service_id,
                    TileAssignment.x == x,
                    TileAssignment.y == y,
                    TileAssignment.z == z,
                )
            ).scalar_one_or_none()

            if not tile:
                raise TileNotAssignedError(
                    f"No tile assignment found for {x},{y},{z} in service {service_id}"
                )

            # Store tile info before deletion
            tile_info = {
                "x": tile.x,
                "y": tile.y,
                "z": tile.z,
                "stage": "released",
            }

            # Delete the tile assignment regardless of its state
            session.delete(tile)
            session.commit()

            return tile_info
