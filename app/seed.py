from uuid import NAMESPACE_URL, uuid5

from app.db import SessionFactory
from app.inventory.models import Product, Warehouse
from app.inventory.schemas import MovementInput
from app.inventory.service import create_movement, execute_command


def load_demo(session) -> dict:
    warehouses = [Warehouse(name="Север"), Warehouse(name="Юг")]
    products = [
        Product(sku="SKU-101", name="Органайзер"),
        Product(sku="SKU-102", name="Контейнер"),
        Product(sku="SKU-103", name="Корзина"),
    ]
    session.add_all([*warehouses, *products])
    session.flush()
    for product, amounts in zip(products, [(10, 6), (5, 0), (0, 4)], strict=True):
        for warehouse, quantity in zip(warehouses, amounts, strict=True):
            if quantity:
                create_movement(
                    session,
                    MovementInput(
                        kind="receipt",
                        product_id=product.id,
                        quantity=quantity,
                        destination_id=warehouse.id,
                        reference="Начальный приход",
                    ),
                )
    return {"loaded": True}


def seed():
    with SessionFactory() as session:
        execute_command(
            session,
            uuid5(NAMESPACE_URL, "checkstock/demo/v1"),
            {"operation": "demo_v1"},
            lambda: load_demo(session),
        )


if __name__ == "__main__":
    seed()
