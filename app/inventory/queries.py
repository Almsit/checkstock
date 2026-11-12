from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, aliased

from app.inventory.models import Balance, Movement, Product, StockEvent, Warehouse
from app.inventory.service import InventoryError, movement_data, warehouse_data


def get_products(
    session: Session, query: str = "", after: int | None = None, limit: int = 50
) -> dict:
    statement = select(Product)
    if query.strip():
        term = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(
            Product.sku.ilike(f"%{term}%", escape="\\")
            | Product.name.ilike(f"%{term}%", escape="\\")
        )
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    if after is not None:
        statement = statement.where(Product.id > after)
    rows = session.scalars(statement.order_by(Product.id).limit(limit + 1)).all()
    return {
        "items": [{"id": row.id, "sku": row.sku, "name": row.name} for row in rows[:limit]],
        "total": total,
        "next": rows[limit - 1].id if len(rows) > limit else None,
    }


def get_balances(session: Session, product_ids: list[int]) -> list[dict]:
    return [
        {
            "warehouse_id": row.warehouse_id,
            "product_id": row.product_id,
            "on_hand": row.on_hand,
            "reserved": row.reserved,
            "available": row.on_hand - row.reserved,
        }
        for row in session.scalars(
            select(Balance)
            .where(Balance.product_id.in_(product_ids))
            .order_by(Balance.product_id, Balance.warehouse_id)
        )
    ]


def product_stock(session: Session, product_id: int) -> list[dict]:
    if session.get(Product, product_id) is None:
        raise InventoryError("Товар не найден", 404)
    return get_balances(session, [product_id])


def get_state(session: Session, query: str = "", after: int | None = None, limit: int = 50) -> dict:
    session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    session.execute(text("SET TRANSACTION READ ONLY"))
    warehouses = [
        warehouse_data(row) for row in session.scalars(select(Warehouse).order_by(Warehouse.id))
    ]
    page = get_products(session, query, after, limit)
    product_ids = [row["id"] for row in page["items"]]
    balances = get_balances(session, product_ids)
    warehouse_totals = [
        {
            "warehouse_id": warehouse_id,
            "on_hand": on_hand,
            "reserved": reserved,
            "available": on_hand - reserved,
        }
        for warehouse_id, on_hand, reserved in session.execute(
            select(
                Balance.warehouse_id, func.sum(Balance.on_hand), func.sum(Balance.reserved)
            ).group_by(Balance.warehouse_id)
        )
    ]
    quantities = [
        {"product_id": product_id, "kind": kind, "status": "in_transit", "quantity": quantity}
        for product_id, kind, quantity in session.execute(
            select(Movement.product_id, Movement.kind, func.sum(Movement.quantity))
            .where(Movement.product_id.in_(product_ids), Movement.status == "in_transit")
            .group_by(Movement.product_id, Movement.kind)
        )
    ]
    completed_filter = ((Movement.kind == "fbo") & (Movement.status == "received")) | (
        (Movement.kind == "fbs") & (Movement.status == "delivered")
    )
    totals = {
        "on_hand": sum(row["on_hand"] for row in warehouse_totals),
        "available": sum(row["available"] for row in warehouse_totals),
        "reserved": sum(row["reserved"] for row in warehouse_totals),
        "transfer": 0,
        "fbo": 0,
        "fbs": 0,
        "accepted_fbo": 0,
        "delivered_fbs": 0,
    }
    for kind, status, quantity in session.execute(
        select(Movement.kind, Movement.status, func.sum(Movement.quantity))
        .where((Movement.status == "in_transit") | completed_filter)
        .group_by(Movement.kind, Movement.status)
    ):
        key = (
            kind if status == "in_transit" else "accepted_fbo" if kind == "fbo" else "delivered_fbs"
        )
        totals[key] = quantity
    completed = [
        {"product_id": product_id, "kind": kind, "destination": destination, "quantity": quantity}
        for product_id, kind, destination, quantity in session.execute(
            select(
                Movement.product_id,
                Movement.kind,
                Movement.destination,
                func.sum(Movement.quantity),
            )
            .where(Movement.product_id.in_(product_ids), completed_filter)
            .group_by(Movement.product_id, Movement.kind, Movement.destination)
            .order_by(Movement.product_id, Movement.kind, Movement.destination)
            .limit(101)
        )
    ]
    return {
        "warehouses": warehouses,
        "products": page["items"],
        "balances": balances,
        "quantities": quantities,
        "totals": totals,
        "warehouse_totals": warehouse_totals,
        "completed": completed[:100],
        "completed_has_more": len(completed) > 100,
        "product_total": page["total"],
        "next": page["next"],
    }


def get_movements(session: Session, before: int | None, limit: int) -> dict:
    source = aliased(Warehouse)
    destination = aliased(Warehouse)
    query = (
        select(Movement, Product.sku, Product.name, source.name, destination.name)
        .join(Product, Product.id == Movement.product_id)
        .outerjoin(source, source.id == Movement.source_id)
        .outerjoin(destination, destination.id == Movement.destination_id)
        .where(Movement.status.in_(["reserved", "in_transit"]))
        .order_by(Movement.number.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(Movement.number < before)
    rows = session.execute(query).all()
    items = [
        {
            **movement_data(movement),
            "sku": sku,
            "product_name": name,
            "source_name": source_name,
            "destination_name": destination_name or movement.destination,
        }
        for movement, sku, name, source_name, destination_name in rows[:limit]
    ]
    return {"items": items, "next": rows[limit - 1][0].number if len(rows) > limit else None}


def get_history(session: Session, before: int | None, limit: int) -> dict:
    query = (
        select(StockEvent, Movement, Product)
        .join(Movement, Movement.id == StockEvent.movement_id)
        .join(Product, Product.id == Movement.product_id)
        .order_by(StockEvent.id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(StockEvent.id < before)
    rows = session.execute(query).all()
    items = [
        {
            "id": event.id,
            "movement_id": str(movement.id),
            "kind": movement.kind,
            "action": event.action,
            "sku": product.sku,
            "product_name": product.name,
            "quantity": movement.quantity,
            "source": event.source_label,
            "destination": event.destination_label,
            "reference": movement.reference,
            "on_hand_delta": event.on_hand_delta,
            "reserved_delta": event.reserved_delta,
            "created_at": event.created_at.isoformat(),
        }
        for event, movement, product in rows[:limit]
    ]
    return {"items": items, "next": rows[limit - 1][0].id if len(rows) > limit else None}
