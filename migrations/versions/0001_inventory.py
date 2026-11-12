import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku"),
    )
    op.create_table(
        "warehouses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_warehouse_name"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_warehouse_name", "warehouses", [sa.literal_column("lower(name)")], unique=True
    )
    op.create_table(
        "balances",
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("on_hand", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("reserved", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint("on_hand >= 0", name="ck_balance_on_hand"),
        sa.CheckConstraint("reserved >= 0 AND reserved <= on_hand", name="ck_balance_reserved"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
        ),
        sa.PrimaryKeyConstraint("warehouse_id", "product_id"),
    )
    op.create_index("ix_balance_product", "balances", ["product_id"], unique=False)
    op.create_table(
        "movements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("destination_id", sa.Integer(), nullable=True),
        sa.Column("destination", sa.String(length=120), nullable=True),
        sa.Column("reference", sa.String(length=80), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'receipt' AND source_id IS NULL AND destination_id IS NOT NULL "
            "AND destination IS NULL) OR "
            "(kind = 'transfer' AND source_id IS NOT NULL AND destination_id IS NOT NULL "
            "AND source_id <> destination_id AND destination IS NULL) OR "
            "(kind IN ('fbs', 'fbo') AND source_id IS NOT NULL AND destination_id IS NULL "
            "AND destination IS NOT NULL AND length(trim(destination)) > 0)",
            name="ck_movement_route",
        ),
        sa.CheckConstraint(
            "(kind = 'receipt' AND status = 'received') OR "
            "(kind IN ('transfer', 'fbo') AND status IN ('in_transit', 'received')) OR "
            "(kind = 'fbs' AND status IN ('reserved', 'in_transit', 'cancelled', 'delivered'))",
            name="ck_movement_status",
        ),
        sa.CheckConstraint("quantity > 0 AND quantity <= 1000000", name="ck_movement_quantity"),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["warehouses.id"],
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["warehouses.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_movement_created", "movements", ["created_at", "id"], unique=False)
    op.create_index("ix_movement_destination", "movements", ["destination_id"], unique=False)
    op.create_index("ix_movement_product", "movements", ["product_id"], unique=False)
    op.create_index("ix_movement_source", "movements", ["source_id"], unique=False)
    op.create_index("ix_movement_status_kind", "movements", ["status", "kind"], unique=False)
    op.create_table(
        "stock_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("movement_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=12), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=True),
        sa.Column("on_hand_delta", sa.BigInteger(), nullable=False),
        sa.Column("reserved_delta", sa.BigInteger(), nullable=False),
        sa.Column("source_label", sa.String(length=120), nullable=False),
        sa.Column("destination_label", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('receive', 'reserve', 'ship', 'cancel', 'deliver')", name="ck_event_action"
        ),
        sa.CheckConstraint(
            "warehouse_id IS NOT NULL OR (on_hand_delta = 0 AND reserved_delta = 0)",
            name="ck_event_location",
        ),
        sa.ForeignKeyConstraint(
            ["movement_id"],
            ["movements.id"],
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_warehouse", "stock_events", ["warehouse_id"], unique=False)
    op.create_index("uq_event_action", "stock_events", ["movement_id", "action"], unique=True)
    op.execute("""
        CREATE FUNCTION protect_stock_events() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Stock history is immutable';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER stock_events_immutable BEFORE UPDATE OR DELETE ON stock_events
        FOR EACH ROW EXECUTE FUNCTION protect_stock_events()
    """)


def downgrade():
    op.drop_index("uq_event_action", table_name="stock_events")
    op.drop_index("ix_event_warehouse", table_name="stock_events")
    op.drop_table("stock_events")
    op.drop_index("ix_movement_status_kind", table_name="movements")
    op.drop_index("ix_movement_source", table_name="movements")
    op.drop_index("ix_movement_product", table_name="movements")
    op.drop_index("ix_movement_destination", table_name="movements")
    op.drop_index("ix_movement_created", table_name="movements")
    op.drop_table("movements")
    op.drop_index("ix_balance_product", table_name="balances")
    op.drop_table("balances")
    op.drop_index("uq_warehouse_name", table_name="warehouses")
    op.drop_table("warehouses")
    op.drop_table("products")
    op.drop_table("commands")
    op.execute("DROP FUNCTION protect_stock_events()")
