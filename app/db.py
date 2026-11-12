import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

engine = create_engine(
    os.environ["DATABASE_URL"],
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
    pool_timeout=5,
    connect_args={
        "connect_timeout": 5,
        "options": "-c lock_timeout=5000 -c statement_timeout=15000",
    },
)
SessionFactory = sessionmaker(engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionFactory() as session:
        yield session
