from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def parallel_requests(requests):
    barrier = Barrier(len(requests))

    def send(spec):
        method, path, body, key = spec
        with TestClient(app, base_url="http://localhost") as client:
            barrier.wait(timeout=10)
            return client.request(
                method, f"/api{path}", json=body, headers={"Idempotency-Key": str(key)}
            )

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        return list(executor.map(send, requests))


def test_competing_reservations_cannot_oversell(balance):
    body = {"kind": "fbs", "product_id": 1, "source_id": 1, "quantity": 4, "destination": "Клиент"}
    responses = parallel_requests([("POST", "/movements", body, uuid4()) for _ in range(3)])
    assert sorted(row.status_code for row in responses) == [201, 201, 409]
    assert balance()["reserved"] == 8
    assert balance()["available"] == 2


def test_parallel_retries_create_one_receipt(balance, client):
    key = uuid4()
    body = {"kind": "receipt", "product_id": 2, "destination_id": 2, "quantity": 11}
    responses = parallel_requests([("POST", "/movements", body, key) for _ in range(6)])
    assert all(row.status_code == 201 for row in responses)
    assert len({row.json()["id"] for row in responses}) == 1
    assert balance(2, 2)["on_hand"] == 11
    assert len(client.get("/api/history").json()["items"]) == 5


def test_first_receipts_to_empty_balance_are_not_lost(balance):
    body = {"kind": "receipt", "product_id": 2, "destination_id": 2, "quantity": 2}
    responses = parallel_requests([("POST", "/movements", body, uuid4()) for _ in range(6)])
    assert all(row.status_code == 201 for row in responses)
    assert balance(2, 2)["on_hand"] == 12


def test_parallel_receiving_is_idempotent_with_different_keys(post, balance, state):
    movement = post(
        "/movements",
        {"kind": "transfer", "product_id": 1, "source_id": 1, "destination_id": 2, "quantity": 5},
    ).json()
    path = f"/movements/{movement['id']}/actions"
    responses = parallel_requests(
        [("POST", path, {"action": "receive"}, uuid4()) for _ in range(6)]
    )
    assert all(row.status_code == 200 for row in responses)
    assert balance(2)["on_hand"] == 11
    assert state()["totals"]["transfer"] == 0


def test_shipping_and_cancelling_a_reserve_are_mutually_exclusive(post, balance, state):
    movement = post(
        "/movements",
        {"kind": "fbs", "product_id": 1, "source_id": 1, "quantity": 5, "destination": "Клиент"},
    ).json()
    path = f"/movements/{movement['id']}/actions"
    responses = parallel_requests(
        [("POST", path, {"action": action}, uuid4()) for action in ["ship", "cancel"]]
    )
    assert sorted(row.status_code for row in responses) == [200, 409]
    assert balance()["reserved"] == 0
    assert (balance()["on_hand"], state()["totals"]["fbs"]) in [(5, 5), (10, 0)]


def test_opposite_transfers_and_parallel_arrivals_do_not_deadlock(balance):
    requests = [
        (
            "POST",
            "/movements",
            {
                "kind": "transfer",
                "product_id": 1,
                "source_id": source,
                "destination_id": destination,
                "quantity": 3,
            },
            uuid4(),
        )
        for source, destination in [(1, 2), (2, 1)]
    ]
    responses = parallel_requests(requests)
    assert all(row.status_code == 201 for row in responses)
    arrivals = parallel_requests(
        [
            ("POST", f"/movements/{row.json()['id']}/actions", {"action": "receive"}, uuid4())
            for row in responses
        ]
    )
    assert all(row.status_code == 200 for row in arrivals)
    assert balance(1)["on_hand"] == 10
    assert balance(2)["on_hand"] == 6


def test_archive_racing_a_new_reservation_keeps_consistent_state(state):
    responses = parallel_requests(
        [
            ("DELETE", "/warehouses/1", None, uuid4()),
            (
                "POST",
                "/movements",
                {
                    "kind": "fbs",
                    "product_id": 1,
                    "source_id": 1,
                    "quantity": 2,
                    "destination": "Клиент",
                },
                uuid4(),
            ),
        ]
    )
    assert responses[0].status_code == 200
    assert responses[1].status_code in [201, 409]
    assert state()["warehouses"][0]["archived"] is True
    assert state()["totals"]["reserved"] == (2 if responses[1].status_code == 201 else 0)


def test_concurrent_duplicate_warehouse_names_are_rejected(state):
    responses = parallel_requests(
        [("POST", "/warehouses", {"name": "Восток"}, uuid4()) for _ in range(2)]
    )
    assert sorted(row.status_code for row in responses) == [201, 409]
    assert len(state()["warehouses"]) == 3
