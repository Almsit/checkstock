import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column("movements", sa.Column("number", sa.BigInteger(), sa.Identity(), nullable=False))
    op.create_unique_constraint("uq_movement_number", "movements", ["number"])
    op.drop_index("ix_movement_created", table_name="movements")
    op.create_index(
        "ix_movement_open",
        "movements",
        [sa.text("number DESC")],
        postgresql_where=sa.text("status IN ('reserved', 'in_transit')"),
    )
    op.create_index(
        "ix_product_sku_search",
        "products",
        ["sku"],
        postgresql_using="gin",
        postgresql_ops={"sku": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_product_name_search",
        "products",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )


def downgrade():
    op.drop_index("ix_product_name_search", table_name="products")
    op.drop_index("ix_product_sku_search", table_name="products")
    op.drop_index("ix_movement_open", table_name="movements")
    op.create_index("ix_movement_created", "movements", ["created_at", "id"])
    op.drop_constraint("uq_movement_number", "movements", type_="unique")
    op.drop_column("movements", "number")
