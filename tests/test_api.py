from uuid import uuid4

import pytest


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, "2", 1_000_001, None])
def test_invalid_quantities_are_rejected(post, state, quantity):
    before = state()
    assert (
        post(
            "/movements",
            {"kind": "receipt", "product_id": 1, "destination_id": 1, "quantity": quantity},
        ).status_code
        == 422
    )
    assert state() == before


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "transfer", "source_id": 1, "destination_id": 1},
        {"kind": "receipt", "source_id": 1, "destination_id": 2},
        {"kind": "fbo", "source_id": 1, "destination_id": 2, "destination": "WB"},
        {"kind": "fbs", "source_id": 1},
        {"kind": "fbo", "source_id": 1, "destination": "   "},
        {"kind": "receipt", "destination_id": 1, "status": "received"},
        {"kind": "transfer", "source_id": True, "destination_id": 2},
        {"kind": "receipt", "destination_id": 1, "reference": "x" * 81},
    ],
)
def test_invalid_routes_and_unknown_fields_are_rejected(post, body):
    assert post("/movements", {"product_id": 1, "quantity": 1, **body}).status_code == 422


@pytest.mark.parametrize("name", ["", "  ", "x" * 81, "Склад\nСевер", "\x00Склад"])
def test_invalid_warehouse_names_are_rejected(post, name):
    assert post("/warehouses", {"name": name}).status_code == 422


def test_missing_resources_do_not_write_data(post, state):
    before = state()
    assert (
        post(
            "/movements", {"kind": "receipt", "product_id": 999, "destination_id": 1, "quantity": 1}
        ).status_code
        == 404
    )
    assert (
        post(
            "/movements", {"kind": "receipt", "product_id": 1, "destination_id": 999, "quantity": 1}
        ).status_code
        == 404
    )
    assert post(f"/movements/{uuid4()}/actions", {"action": "receive"}).status_code == 404
    assert state() == before


def test_idempotency_keys_are_mandatory_and_not_shared_between_operations(client, post):
    assert client.post("/api/warehouses", json={"name": "Восток"}).status_code == 422
    assert (
        client.post(
            "/api/warehouses", json={"name": "Восток"}, headers={"Idempotency-Key": "invalid"}
        ).status_code
        == 422
    )
    key = uuid4()
    assert post("/warehouses", {"name": "Восток"}, key).status_code == 201
    assert (
        post(
            "/movements",
            {"kind": "receipt", "product_id": 1, "destination_id": 1, "quantity": 1},
            key,
        ).status_code
        == 409
    )


def test_untrusted_origins_hosts_content_types_and_large_payloads(client):
    headers = {"Idempotency-Key": str(uuid4())}
    assert (
        client.post(
            "/api/warehouses",
            json={"name": "Восток"},
            headers={**headers, "Origin": "https://untrusted.invalid"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/warehouses",
            json={"name": "Восток"},
            headers={**headers, "Sec-Fetch-Site": "cross-site"},
        ).status_code
        == 403
    )
    assert client.get("/api/state", headers={"Host": "untrusted.invalid"}).status_code == 400
    assert client.post("/api/warehouses", content="name=Восток", headers=headers).status_code == 415
    assert (
        client.post(
            "/api/warehouses",
            content="x" * 9000,
            headers={**headers, "Content-Type": "application/json"},
        ).status_code
        == 413
    )


def test_same_origin_and_literal_sql_text_are_safe(client, post):
    name = "Склад'); DROP TABLE products;--"
    response = client.post(
        "/api/warehouses",
        json={"name": name},
        headers={"Idempotency-Key": str(uuid4()), "Origin": "http://localhost"},
    )
    assert response.status_code == 201
    assert response.json()["name"] == name
    assert len(client.get("/api/state").json()["products"]) == 3
    assert post("/warehouses", {"name": '<img src=x onerror="alert(1)">'}).status_code == 201


def test_static_assets_security_headers_and_health(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'lang="ru"' in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    for path in ["css/app.css", "js/app.js", "js/api.js", "js/forms.js", "js/views.js", "icon.svg"]:
        assert client.get(f"/static/{path}").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/static/../db.py").status_code == 404


def test_page_limits_are_bounded(client):
    for query in ["limit=0", "limit=101", "before=-1"]:
        assert client.get(f"/api/history?{query}").status_code == 422
