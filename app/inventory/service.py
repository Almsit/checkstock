import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.inventory.models import Balance, Command, Movement, Product, StockEvent, Warehouse
from app.inventory.schemas import MovementInput


class InventoryError(Exception):
    def __init__(self, message: str, status: int = 409, **details):
        self.message = message
        self.status = status
        self.details = details


def execute_command(
    session: Session, key: UUID, payload: dict, operation: Callable[[], dict]
) -> dict:
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    with session.begin():
        claimed = session.scalar(
            insert(Command)
            .values(id=key, fingerprint=fingerprint, response={})
            .on_conflict_do_nothing(index_elements=[Command.id])
            .returning(Command.id)
        )
        command = session.get(Command, key)
        if not claimed:
            if command.fingerprint != fingerprint:
                raise InventoryError("Ключ запроса уже использован для другого действия")
            return command.response
        response = operation()
        session.flush()
        command.response = response
    return response


def warehouse_data(warehouse: Warehouse) -> dict:
    return {
        "id": warehouse.id,
        "name": warehouse.name,
        "archived": warehouse.archived_at is not None,
    }


def save_warehouse(session: Session, name: str, warehouse_id: int | None = None) -> dict:
    duplicate = session.scalar(
        select(Warehouse.id).where(func.lower(Warehouse.name) == name.lower())
    )
    if duplicate is not None and duplicate != warehouse_id:
        raise InventoryError("ФФ с таким названием уже существует, в том числе в архиве")
    if warehouse_id is None:
        warehouse = Warehouse(name=name)
        session.add(warehouse)
    else:
        warehouse = session.scalar(
            select(Warehouse).where(Warehouse.id == warehouse_id).with_for_update()
        )
        if warehouse is None:
            raise InventoryError("ФФ не найден", 404)
        warehouse.name = name
    session.flush()
    return warehouse_data(warehouse)


def archive_warehouse(session: Session, warehouse_id: int) -> dict:
    warehouse = session.scalar(
        select(Warehouse).where(Warehouse.id == warehouse_id).with_for_update()
    )
    if warehouse is None:
        raise InventoryError("ФФ не найден", 404)
    if warehouse.archived_at is None:
        warehouse.archived_at = datetime.now(UTC)
    return warehouse_data(warehouse)


def lock_warehouses(session: Session, ids: list[int]) -> dict[int, Warehouse]:
    warehouses = {
        warehouse.id: warehouse
        for warehouse in session.scalars(
            select(Warehouse)
            .where(Warehouse.id.in_(ids))
            .order_by(Warehouse.id)
            .with_for_update(read=True)
        )
    }
    if len(warehouses) != len(set(ids)):
        raise InventoryError("ФФ не найден", 404)
    return warehouses


def lock_balance(session: Session, warehouse_id: int, product_id: int) -> Balance:
    session.execute(
        insert(Balance)
        .values(warehouse_id=warehouse_id, product_id=product_id)
        .on_conflict_do_nothing()
    )
    return session.scalar(
        select(Balance)
        .where(Balance.warehouse_id == warehouse_id, Balance.product_id == product_id)
        .with_for_update()
    )


def movement_data(movement: Movement) -> dict:
    return {
        "id": str(movement.id),
        "number": movement.number,
        "kind": movement.kind,
        "status": movement.status,
        "product_id": movement.product_id,
        "quantity": movement.quantity,
        "source_id": movement.source_id,
        "destination_id": movement.destination_id,
        "destination": movement.destination,
        "reference": movement.reference,
        "created_at": movement.created_at.isoformat(),
    }


def record_event(
    session: Session,
    movement: Movement,
    action: str,
    warehouses: dict[int, Warehouse],
    balance: Balance | None = None,
    on_hand_delta: int = 0,
    reserved_delta: int = 0,
) -> None:
    if balance is not None:
        balance.on_hand += on_hand_delta
        balance.reserved += reserved_delta
    session.add(
        StockEvent(
            movement_id=movement.id,
            action=action,
            warehouse_id=balance.warehouse_id if balance else None,
            on_hand_delta=on_hand_delta,
            reserved_delta=reserved_delta,
            source_label=warehouses[movement.source_id].name if movement.source_id else "Поставщик",
            destination_label=(
                warehouses[movement.destination_id].name
                if movement.destination_id
                else movement.destination
            ),
        )
    )


def create_movement(session: Session, data: MovementInput) -> dict:
    if session.get(Product, data.product_id) is None:
        raise InventoryError("Товар не найден", 404)
    warehouses = lock_warehouses(
        session, [value for value in [data.source_id, data.destination_id] if value]
    )
    if data.destination_id and warehouses[data.destination_id].archived_at:
        raise InventoryError("Новые приходы и перемещения на архивный ФФ недоступны")
    if data.source_id and warehouses[data.source_id].archived_at and data.kind != "transfer":
        raise InventoryError(
            "Из архивного ФФ можно только переместить свободный товар на другой ФФ"
        )

    warehouse_id = data.destination_id if data.kind == "receipt" else data.source_id
    balance = lock_balance(session, warehouse_id, data.product_id)
    available = balance.on_hand - balance.reserved
    if data.kind != "receipt" and data.quantity > available:
        locations = [
            {"name": name, "available": on_hand - reserved, "archived": archived is not None}
            for name, on_hand, reserved, archived in session.execute(
                select(Warehouse.name, Balance.on_hand, Balance.reserved, Warehouse.archived_at)
                .join(Balance, Balance.warehouse_id == Warehouse.id)
                .where(Balance.product_id == data.product_id, Warehouse.id != warehouse_id)
                .order_by(Warehouse.id)
            )
        ]
        raise InventoryError(
            f"На ФФ «{warehouses[warehouse_id].name}» доступно {available} шт., "
            f"нужно {data.quantity} шт. Сначала переместите и примите недостающий товар.",
            code="insufficient_stock",
            available=available,
            locations=locations,
        )

    status = {"receipt": "received", "fbs": "reserved"}.get(data.kind, "in_transit")
    movement = Movement(**data.model_dump(), status=status)
    session.add(movement)
    session.flush()
    if data.kind == "receipt":
        record_event(session, movement, "receive", warehouses, balance, data.quantity)
    elif data.kind == "fbs":
        record_event(
            session, movement, "reserve", warehouses, balance, reserved_delta=data.quantity
        )
    else:
        record_event(session, movement, "ship", warehouses, balance, -data.quantity)
    return movement_data(movement)


def change_movement(session: Session, movement_id: UUID, action: str) -> dict:
    movement = session.scalar(select(Movement).where(Movement.id == movement_id).with_for_update())
    if movement is None:
        raise InventoryError("Операция не найдена", 404)
    if session.scalar(
        select(StockEvent.id).where(
            StockEvent.movement_id == movement_id, StockEvent.action == action
        )
    ):
        return movement_data(movement)

    transitions = {
        ("fbs", "reserved", "ship"): "in_transit",
        ("fbs", "reserved", "cancel"): "cancelled",
        ("fbs", "in_transit", "deliver"): "delivered",
        ("transfer", "in_transit", "receive"): "received",
        ("fbo", "in_transit", "receive"): "received",
    }
    status = transitions.get((movement.kind, movement.status, action))
    if status is None:
        raise InventoryError("Это действие недоступно для текущего состояния операции")

    warehouses = lock_warehouses(
        session, [value for value in [movement.source_id, movement.destination_id] if value]
    )
    balance = None
    on_hand_delta = reserved_delta = 0
    if movement.kind == "fbs" and action in {"ship", "cancel"}:
        balance = lock_balance(session, movement.source_id, movement.product_id)
        reserved_delta = -movement.quantity
        if action == "ship":
            on_hand_delta = -movement.quantity
    elif movement.kind == "transfer" and action == "receive":
        balance = lock_balance(session, movement.destination_id, movement.product_id)
        on_hand_delta = movement.quantity

    movement.status = status
    record_event(session, movement, action, warehouses, balance, on_hand_delta, reserved_delta)
    return movement_data(movement)
