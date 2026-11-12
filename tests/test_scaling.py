from sqlalchemy import insert

from app.db import engine
from app.inventory.models import Product


def test_catalog_pagination_bounds_the_stock_response(client):
    with engine.begin() as connection:
        connection.execute(
            insert(Product),
            [{"sku": f"EXTRA-{number:04}", "name": f"Товар {number}"} for number in range(120)],
        )
    first = client.get("/api/state").json()
    assert len(first["products"]) == 50
    assert first["product_total"] == 123
    seen = [row["id"] for row in first["products"]]
    cursor = first["next"]
    while cursor:
        page = client.get(f"/api/state?after={cursor}").json()
        assert len(page["products"]) <= 50
        assert page["totals"]["on_hand"] == 25
        seen.extend(row["id"] for row in page["products"])
        cursor = page["next"]
    assert len(seen) == len(set(seen)) == 123


def test_filter_keeps_global_and_warehouse_totals(client):
    page = client.get("/api/state", params={"q": "корз"}).json()
    assert [row["sku"] for row in page["products"]] == ["SKU-103"]
    assert page["product_total"] == 1
    assert page["totals"]["on_hand"] == 25
    assert {row["warehouse_id"]: row["on_hand"] for row in page["warehouse_totals"]} == {
        1: 15,
        2: 10,
    }
    assert all(row["product_id"] == 3 for row in page["balances"])


def test_search_treats_wildcards_as_literal_text(client):
    with engine.begin() as connection:
        connection.execute(
            insert(Product),
            [
                {"sku": "PERCENT", "name": "Товар 100%"},
                {"sku": "UNDER_SCORE", "name": "Набор"},
            ],
        )
    assert client.get("/api/products", params={"q": "%"}).json()["total"] == 1
    assert client.get("/api/products", params={"q": "_"}).json()["total"] == 1
    assert client.get("/api/products", params={"q": "sku-10"}).json()["total"] == 3
    assert client.get("/api/state", params={"q": "ничего подобного"}).json()["products"] == []


def test_product_lookup_and_stock_do_not_depend_on_the_visible_page(client, post):
    with engine.begin() as connection:
        product_id = connection.scalar(
            insert(Product).values(sku="LAST-900", name="Последний товар").returning(Product.id)
        )
    lookup = client.get("/api/products", params={"q": "LAST-900"}).json()
    assert lookup["items"][0]["id"] == product_id
    assert client.get(f"/api/products/{product_id}/balances").json() == []
    assert (
        post(
            "/movements",
            {"kind": "receipt", "product_id": product_id, "quantity": 3, "destination_id": 2},
        ).status_code
        == 201
    )
    assert client.get(f"/api/products/{product_id}/balances").json()[0]["available"] == 3
    assert client.get("/api/products/99999/balances").status_code == 404


def test_pool_exhaustion_returns_retryable_response(client, monkeypatch):
    from sqlalchemy.exc import TimeoutError

    from app.inventory import queries

    def exhausted(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(queries, "get_state", exhausted)
    response = client.get("/api/state")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"


def test_catalog_limits_and_search_length_are_bounded(client):
    for path in ["/api/state", "/api/products"]:
        assert client.get(path, params={"limit": 101}).status_code == 422
        assert client.get(path, params={"after": -1}).status_code == 422
        assert client.get(path, params={"q": "x" * 161}).status_code == 422
