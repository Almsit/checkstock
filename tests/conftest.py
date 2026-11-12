from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import engine
from app.main import app
from app.seed import seed


@pytest.fixture(scope="session", autouse=True)
def schema():
    if not engine.url.database.endswith("_test"):
        pytest.exit("Tests require a separate database ending in _test")
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def inventory(schema):
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE commands, stock_events, movements, balances, products, warehouses "
                "RESTART IDENTITY CASCADE"
            )
        )
    seed()
    yield
    with engine.connect() as connection:
        differences = connection.execute(
            text("""
            WITH ledger AS (
                SELECT e.warehouse_id, m.product_id, sum(e.on_hand_delta) AS on_hand,
                       sum(e.reserved_delta) AS reserved
                FROM stock_events e JOIN movements m ON m.id = e.movement_id
                WHERE e.warehouse_id IS NOT NULL
                GROUP BY e.warehouse_id, m.product_id
            )
            SELECT * FROM balances b FULL JOIN ledger l USING (warehouse_id, product_id)
            WHERE coalesce(b.on_hand, 0) != coalesce(l.on_hand, 0)
               OR coalesce(b.reserved, 0) != coalesce(l.reserved, 0)
        """)
        ).all()
        assert differences == [], "Balances must reconcile with the independent event ledger"
        quantities = connection.execute(
            text("""
            SELECT p.sku,
                coalesce((SELECT sum(quantity) FROM movements
                          WHERE product_id=p.id AND kind='receipt'), 0) AS received,
                coalesce((SELECT sum(on_hand) FROM balances WHERE product_id=p.id), 0)
                + coalesce((SELECT sum(quantity) FROM movements WHERE product_id=p.id
                    AND (status='in_transit' OR (kind='fbo' AND status='received')
                         OR status='delivered')), 0) AS accounted
            FROM products p
        """)
        ).all()
        assert all(row.received == row.accounted for row in quantities)
        reservations = connection.execute(
            text("""
            SELECT b.warehouse_id, b.product_id FROM balances b
            WHERE b.reserved != coalesce((SELECT sum(m.quantity) FROM movements m
                WHERE m.source_id=b.warehouse_id AND m.product_id=b.product_id
                  AND m.kind='fbs' AND m.status='reserved'), 0)
        """)
        ).all()
        assert reservations == []


@pytest.fixture
def client():
    with TestClient(app, base_url="http://localhost") as client:
        yield client


@pytest.fixture
def post(client):
    def send(path, body, key=None):
        return client.post(
            f"/api{path}", json=body, headers={"Idempotency-Key": str(key or uuid4())}
        )

    return send


@pytest.fixture
def state(client):
    return lambda: client.get("/api/state").json()


@pytest.fixture
def balance(state):
    def get(warehouse=1, product=1):
        return next(
            (
                row
                for row in state()["balances"]
                if row["warehouse_id"] == warehouse and row["product_id"] == product
            ),
            {
                "warehouse_id": warehouse,
                "product_id": product,
                "on_hand": 0,
                "reserved": 0,
                "available": 0,
            },
        )

    return get
