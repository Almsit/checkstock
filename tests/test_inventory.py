from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db import engine
from app.seed import seed


def test_initial_receipts_and_seed_retry(state, client):
    first = state()
    assert [(p["sku"], p["name"]) for p in first["products"]] == [
        ("SKU-101", "Органайзер"),
        ("SKU-102", "Контейнер"),
        ("SKU-103", "Корзина"),
    ]
    assert [(w["name"], w["archived"]) for w in first["warehouses"]] == [
        ("Север", False),
        ("Юг", False),
    ]
    assert {(r["product_id"], r["warehouse_id"]): r["on_hand"] for r in first["balances"]} == {
        (1, 1): 10,
        (1, 2): 6,
        (2, 1): 5,
        (3, 2): 4,
    }
    assert first["totals"]["on_hand"] == first["totals"]["available"] == 25
    assert first["totals"]["reserved"] == 0
    assert len(client.get("/api/history").json()["items"]) == 4
    seed()
    assert state() == first


def test_receipt_and_duplicate_request(post, balance, client):
    key = uuid4()
    body = {"kind": "receipt", "product_id": 3, "destination_id": 1, "quantity": 7}
    first = post("/movements", body, key)
    second = post("/movements", body, key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert balance(1, 3)["on_hand"] == 7
    assert len(client.get("/api/history").json()["items"]) == 5
    assert post("/movements", {**body, "quantity": 8}, key).status_code == 409
    assert balance(1, 3)["on_hand"] == 7


def test_fbs_reserve_ship_deliver(post, balance, state, client):
    response = post(
        "/movements",
        {
            "kind": "fbs",
            "product_id": 1,
            "source_id": 1,
            "quantity": 4,
            "destination": "Клиент WB-17",
        },
    )
    assert response.status_code == 201
    movement = response.json()
    assert movement["status"] == "reserved"
    assert balance() == {
        "warehouse_id": 1,
        "product_id": 1,
        "on_hand": 10,
        "available": 6,
        "reserved": 4,
    }
    assert state()["totals"]["fbs"] == 0
    path = f"/movements/{movement['id']}/actions"
    assert post(path, {"action": "ship"}).json()["status"] == "in_transit"
    assert balance()["on_hand"] == balance()["available"] == 6
    assert balance()["reserved"] == 0
    assert state()["totals"]["fbs"] == 4
    assert post(path, {"action": "ship"}).status_code == 200
    assert balance()["on_hand"] == 6
    assert post(path, {"action": "cancel"}).status_code == 409
    assert post(path, {"action": "deliver"}).json()["status"] == "delivered"
    assert state()["totals"]["fbs"] == 0
    assert state()["totals"]["delivered_fbs"] == 4
    assert post(path, {"action": "deliver"}).status_code == 200
    assert post(path, {"action": "ship"}).json()["status"] == "delivered"
    assert client.get("/api/movements").json()["items"] == []


def test_cancel_reserve_releases_only_its_quantity(post, balance):
    first = post(
        "/movements",
        {"kind": "fbs", "product_id": 1, "source_id": 1, "quantity": 3, "destination": "Заказ 1"},
    ).json()
    post(
        "/movements",
        {"kind": "fbs", "product_id": 1, "source_id": 1, "quantity": 2, "destination": "Заказ 2"},
    )
    assert balance()["reserved"] == 5
    path = f"/movements/{first['id']}/actions"
    assert post(path, {"action": "cancel"}).json()["status"] == "cancelled"
    assert post(path, {"action": "cancel"}).status_code == 200
    assert balance()["on_hand"] == 10
    assert balance()["reserved"] == 2
    assert balance()["available"] == 8
    assert post(path, {"action": "ship"}).status_code == 409


def test_transfer_has_a_real_transit_stage(post, balance, state):
    response = post(
        "/movements",
        {"kind": "transfer", "product_id": 1, "source_id": 1, "destination_id": 2, "quantity": 4},
    )
    assert response.status_code == 201
    movement = response.json()
    assert balance(1)["on_hand"] == 6
    assert balance(2)["on_hand"] == 6
    assert state()["totals"]["on_hand"] == 21
    assert state()["totals"]["transfer"] == 4
    path = f"/movements/{movement['id']}/actions"
    assert post(path, {"action": "receive"}).json()["status"] == "received"
    assert balance(2)["on_hand"] == 10
    assert state()["totals"]["transfer"] == 0
    assert state()["totals"]["on_hand"] == 25
    assert post(path, {"action": "receive"}).status_code == 200
    assert balance(2)["on_hand"] == 10


def test_fbo_arrival_does_not_return_stock_to_fulfillment(post, state, balance):
    movement = post(
        "/movements",
        {
            "kind": "fbo",
            "product_id": 1,
            "source_id": 2,
            "quantity": 6,
            "destination": "WB · Коледино",
        },
    ).json()
    assert balance(2)["on_hand"] == 0
    assert state()["totals"]["fbo"] == 6
    path = f"/movements/{movement['id']}/actions"
    assert post(path, {"action": "receive"}).status_code == 200
    assert post(path, {"action": "receive"}).status_code == 200
    assert state()["totals"]["fbo"] == 0
    assert state()["totals"]["accepted_fbo"] == 6
    assert state()["completed"] == [
        {"product_id": 1, "kind": "fbo", "destination": "WB · Коледино", "quantity": 6}
    ]
    assert balance(2)["on_hand"] == 0


@pytest.mark.parametrize("kind", ["transfer", "fbs", "fbo"])
def test_source_shortage_never_uses_another_warehouse(post, state, balance, kind):
    post(
        "/movements",
        {
            "kind": "fbs",
            "product_id": 1,
            "source_id": 1,
            "quantity": 4,
            "destination": "Первый резерв",
        },
    )
    before = state()
    body = {"kind": kind, "product_id": 1, "source_id": 1, "quantity": 7}
    body.update({"destination_id": 2} if kind == "transfer" else {"destination": "Получатель"})
    response = post("/movements", body)
    assert response.status_code == 409
    assert response.json()["available"] == 6
    assert response.json()["locations"] == [{"name": "Юг", "available": 6, "archived": False}]
    assert state() == before
    transfer = post(
        "/movements",
        {"kind": "transfer", "product_id": 1, "source_id": 2, "destination_id": 1, "quantity": 1},
    ).json()
    assert post("/movements", body).status_code == 409
    post(f"/movements/{transfer['id']}/actions", {"action": "receive"})
    assert post("/movements", body).status_code == 201
    assert balance(2)["on_hand"] == 5


def test_archiving_retains_stock_reservations_and_pending_arrival(client, post, state, balance):
    reserve = post(
        "/movements",
        {"kind": "fbs", "product_id": 1, "source_id": 1, "quantity": 3, "destination": "Клиент"},
    ).json()
    transfer = post(
        "/movements",
        {"kind": "transfer", "product_id": 1, "source_id": 2, "destination_id": 1, "quantity": 2},
    ).json()
    before = state()["totals"]
    url = "/api/warehouses/1"
    assert client.delete(url, headers={"Idempotency-Key": str(uuid4())}).status_code == 200
    assert client.delete(url, headers={"Idempotency-Key": str(uuid4())}).status_code == 200
    assert state()["totals"] == before
    assert state()["warehouses"][0]["archived"] is True
    assert balance()["on_hand"] == 10
    assert (
        post(
            "/movements", {"kind": "receipt", "product_id": 1, "destination_id": 1, "quantity": 1}
        ).status_code
        == 409
    )
    assert (
        post(
            "/movements",
            {
                "kind": "fbs",
                "product_id": 1,
                "source_id": 1,
                "destination": "Новый заказ",
                "quantity": 1,
            },
        ).status_code
        == 409
    )
    assert (
        post(
            "/movements",
            {
                "kind": "fbo",
                "product_id": 1,
                "source_id": 1,
                "destination": "Маркетплейс",
                "quantity": 1,
            },
        ).status_code
        == 409
    )
    assert (
        post(
            "/movements",
            {
                "kind": "transfer",
                "product_id": 1,
                "source_id": 2,
                "destination_id": 1,
                "quantity": 1,
            },
        ).status_code
        == 409
    )
    assert post(f"/movements/{transfer['id']}/actions", {"action": "receive"}).status_code == 200
    assert post(f"/movements/{reserve['id']}/actions", {"action": "ship"}).status_code == 200
    assert balance()["on_hand"] == 9
    outbound = post(
        "/movements",
        {"kind": "transfer", "product_id": 1, "source_id": 1, "destination_id": 2, "quantity": 9},
    )
    assert outbound.status_code == 201
    assert balance()["on_hand"] == 0
    assert (
        post(f"/movements/{outbound.json()['id']}/actions", {"action": "receive"}).status_code
        == 200
    )
    assert balance(2)["on_hand"] == 13


def test_renaming_preserves_historical_location_names(client, state):
    response = client.patch(
        "/api/warehouses/1", json={"name": "Северный ФФ"}, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 200
    seed()
    assert len(state()["warehouses"]) == 2
    assert state()["warehouses"][0]["name"] == "Северный ФФ"
    assert {row["destination"] for row in client.get("/api/history").json()["items"]} == {
        "Север",
        "Юг",
    }


def test_warehouse_names_are_unique_and_zeros_are_valid(post, state, client):
    response = post("/warehouses", {"name": "Восток"})
    assert response.status_code == 201
    assert response.json()["archived"] is False
    assert len(state()["warehouses"]) == 3
    assert post("/warehouses", {"name": "  восток "}).status_code == 409
    client.delete("/api/warehouses/3", headers={"Idempotency-Key": str(uuid4())})
    assert post("/warehouses", {"name": "Восток"}).status_code == 409


def test_failed_event_write_rolls_back_stock_and_command(post, state, monkeypatch):
    from app.inventory import service

    before = state()
    key = uuid4()
    body = {"kind": "receipt", "product_id": 1, "destination_id": 1, "quantity": 10}

    def reject_event(*args, **kwargs):
        raise service.InventoryError("Не удалось записать журнал")

    with monkeypatch.context() as patch:
        patch.setattr(service, "record_event", reject_event)
        assert post("/movements", body, key).status_code == 409
    assert state() == before
    assert post("/movements", body, key).status_code == 201


def test_history_pagination_has_no_duplicates(client, post):
    seen = []
    url = "/api/history?limit=2"
    while url:
        page = client.get(url).json()
        seen.extend(row["id"] for row in page["items"])
        url = f"/api/history?limit=2&before={page['next']}" if page["next"] else None
    assert seen == [4, 3, 2, 1]
    for _ in range(3):
        assert (
            post(
                "/movements",
                {
                    "kind": "fbs",
                    "product_id": 1,
                    "source_id": 1,
                    "quantity": 1,
                    "destination": "Клиент",
                },
            ).status_code
            == 201
        )
    page = client.get("/api/movements?limit=2").json()
    next_page = client.get(f"/api/movements?limit=2&before={page['next']}").json()
    ids = [row["id"] for row in [*page["items"], *next_page["items"]]]
    assert len(ids) == len(set(ids)) == 3
    assert next_page["next"] is None


def test_database_enforces_balance_limits_and_immutable_history():
    from sqlalchemy.exc import DBAPIError, IntegrityError

    for sql in [
        "UPDATE balances SET on_hand=-1",
        "UPDATE balances SET reserved=on_hand+1",
        "UPDATE balances SET reserved=-1",
    ]:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(sql))
    for sql in ["DELETE FROM stock_events", "UPDATE stock_events SET on_hand_delta=0"]:
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text(sql))
