from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Optional

from sqlmodel import Field, Session, SQLModel, create_engine

from config import DB_PATH


class PortfolioState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cash: float
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Position(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(index=True, unique=True)
    qty: float
    avg_cost: float
    realized_pnl: float = 0.0


class Trade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: str = Field(index=True)
    ticker: str = Field(index=True)
    side: str = Field(index=True)
    qty: float
    fill_price: float
    fee: float = 0.0
    note: Optional[str] = None
    skill_used: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)


class Signal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    signal: str = Field(index=True)
    confidence: float
    reason: str
    skill_used: str = "openclaw"
    price_at_signal: Optional[float] = None
    acted_on: bool = False
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)


class BenchmarkSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(index=True, unique=True)
    spy_price: float
    portfolio_value: float
    cash: float
    equity: float
    created_at: datetime = Field(default_factory=datetime.utcnow)


engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
