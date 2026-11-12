from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("uq_warehouse_name", func.lower(name), unique=True),
        CheckConstraint("length(trim(name)) > 0", name="ck_warehouse_name"),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    __table_args__ = (
        Index(
            "ix_product_sku_search",
            "sku",
            postgresql_using="gin",
            postgresql_ops={"sku": "gin_trgm_ops"},
        ),
        Index(
            "ix_product_name_search",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class Balance(Base):
    __tablename__ = "balances"

    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    on_hand: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    reserved: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    __table_args__ = (
        CheckConstraint("on_hand >= 0", name="ck_balance_on_hand"),
        CheckConstraint("reserved >= 0 AND reserved <= on_hand", name="ck_balance_reserved"),
        Index("ix_balance_product", "product_id"),
    )


class Movement(Base):
    __tablename__ = "movements"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    number: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    kind: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(12))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(BigInteger)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    destination_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    destination: Mapped[str | None] = mapped_column(String(120))
    reference: Mapped[str] = mapped_column(String(80), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("quantity > 0 AND quantity <= 1000000", name="ck_movement_quantity"),
        CheckConstraint(
            "(kind = 'receipt' AND status = 'received') OR "
            "(kind IN ('transfer', 'fbo') AND status IN ('in_transit', 'received')) OR "
            "(kind = 'fbs' AND status IN ('reserved', 'in_transit', 'cancelled', 'delivered'))",
            name="ck_movement_status",
        ),
        CheckConstraint(
            "(kind = 'receipt' AND source_id IS NULL AND destination_id IS NOT NULL "
            "AND destination IS NULL) OR "
            "(kind = 'transfer' AND source_id IS NOT NULL AND destination_id IS NOT NULL "
            "AND source_id <> destination_id AND destination IS NULL) OR "
            "(kind IN ('fbs', 'fbo') AND source_id IS NOT NULL AND destination_id IS NULL "
            "AND destination IS NOT NULL AND length(trim(destination)) > 0)",
            name="ck_movement_route",
        ),
        Index("ix_movement_status_kind", "status", "kind"),
        Index("ix_movement_product", "product_id"),
        Index("ix_movement_source", "source_id"),
        Index("ix_movement_destination", "destination_id"),
        Index(
            "ix_movement_open",
            number.desc(),
            postgresql_where=status.in_(["reserved", "in_transit"]),
        ),
    )


class StockEvent(Base):
    __tablename__ = "stock_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    movement_id: Mapped[UUID] = mapped_column(ForeignKey("movements.id"))
    action: Mapped[str] = mapped_column(String(12))
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"))
    on_hand_delta: Mapped[int] = mapped_column(BigInteger)
    reserved_delta: Mapped[int] = mapped_column(BigInteger)
    source_label: Mapped[str] = mapped_column(String(120))
    destination_label: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        Index("uq_event_action", "movement_id", "action", unique=True),
        Index("ix_event_warehouse", "warehouse_id"),
        CheckConstraint(
            "action IN ('receive', 'reserve', 'ship', 'cancel', 'deliver')",
            name="ck_event_action",
        ),
        CheckConstraint(
            "warehouse_id IS NOT NULL OR (on_hand_delta = 0 AND reserved_delta = 0)",
            name="ck_event_location",
        ),
    )


class Command(Base):
    __tablename__ = "commands"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
