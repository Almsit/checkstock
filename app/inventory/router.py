from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.inventory import queries, service
from app.inventory.schemas import ActionInput, MovementInput, WarehouseInput

router = APIRouter(prefix="/api")
Database = Annotated[Session, Depends(get_session)]
CommandKey = Annotated[UUID, Header(alias="Idempotency-Key")]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageCursor = Annotated[int | None, Query(ge=1)]
Search = Annotated[str, Query(max_length=160)]


@router.get("/state")
def state(session: Database, q: Search = "", after: PageCursor = None, limit: PageLimit = 50):
    return queries.get_state(session, q, after, limit)


@router.get("/products")
def products(session: Database, q: Search = "", after: PageCursor = None, limit: PageLimit = 50):
    return queries.get_products(session, q, after, limit)


@router.get("/products/{product_id}/balances")
def product_stock(product_id: int, session: Database):
    return queries.product_stock(session, product_id)


@router.get("/movements")
def movements(session: Database, before: PageCursor = None, limit: PageLimit = 25):
    return queries.get_movements(session, before, limit)


@router.get("/history")
def history(session: Database, before: PageCursor = None, limit: PageLimit = 30):
    return queries.get_history(session, before, limit)


@router.post("/warehouses", status_code=201)
def create_warehouse(data: WarehouseInput, key: CommandKey, session: Database):
    return service.execute_command(
        session,
        key,
        {"operation": "create_warehouse", **data.model_dump()},
        lambda: service.save_warehouse(session, data.name),
    )


@router.patch("/warehouses/{warehouse_id}")
def rename_warehouse(warehouse_id: int, data: WarehouseInput, key: CommandKey, session: Database):
    return service.execute_command(
        session,
        key,
        {"operation": "rename_warehouse", "id": warehouse_id, **data.model_dump()},
        lambda: service.save_warehouse(session, data.name, warehouse_id),
    )


@router.delete("/warehouses/{warehouse_id}")
def archive_warehouse(warehouse_id: int, key: CommandKey, session: Database):
    return service.execute_command(
        session,
        key,
        {"operation": "archive_warehouse", "id": warehouse_id},
        lambda: service.archive_warehouse(session, warehouse_id),
    )


@router.post("/movements", status_code=201)
def create_movement(data: MovementInput, key: CommandKey, session: Database):
    return service.execute_command(
        session,
        key,
        {"operation": "create_movement", **data.model_dump()},
        lambda: service.create_movement(session, data),
    )


@router.post("/movements/{movement_id}/actions")
def change_movement(movement_id: UUID, data: ActionInput, key: CommandKey, session: Database):
    return service.execute_command(
        session,
        key,
        {"operation": data.action, "id": str(movement_id)},
        lambda: service.change_movement(session, movement_id, data.action),
    )
