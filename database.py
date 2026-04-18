from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import inspect, text
from sqlmodel import Field, Session, SQLModel, create_engine

from config import DB_PATH


class PortfolioState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_name: str = Field(default="default", index=True)
    cash: float
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Position(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_name: str = Field(default="default", index=True)
    ticker: str = Field(index=True)
    qty: float
    avg_cost: float
    realized_pnl: float = 0.0


class Trade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_name: str = Field(default="default", index=True)
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
    screen_scope: Optional[str] = None
    screen_label: Optional[str] = None
    universe: Optional[str] = None
    watchlist_member: bool = False
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


class PipelineRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_name: str = Field(default="siriv5", index=True)
    mode: str = Field(index=True)
    status: str = Field(index=True)
    universe_size: int = 0
    candidates_considered: int = 0
    proposals_created: int = 0
    proposals_executed: int = 0
    summary: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class TradeProposal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    account_name: str = Field(default="siriv5", index=True)
    ticker: str = Field(index=True)
    side: str = Field(index=True)
    signal: str
    confidence: float
    proposed_qty: float
    reference_price: float
    target_position_pct: float
    status: str = Field(index=True)
    rationale: str
    risk_notes: Optional[str] = None
    execution_order_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class TradePlan(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, index=True)
    account_name: str = Field(default="siriv5", index=True)
    ticker: str = Field(index=True)
    thesis_type: str = Field(index=True)
    timeframe: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    conviction: float
    status: str = Field(default="planned", index=True)
    lifecycle_stage: str = Field(default="new", index=True)
    parent_trade_plan_id: Optional[int] = Field(default=None, index=True)
    rationale: str
    source_context: Optional[str] = None
    linked_position_qty: Optional[float] = None
    last_reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class DerivativeIdea(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, index=True)
    account_name: str = Field(default="siriv5", index=True)
    ticker: str = Field(index=True)
    idea_type: str = Field(index=True)
    structure: str
    rationale: str
    risk_note: str
    conviction: float
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


def _ensure_signal_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "signal" not in tables:
        return

    existing = {column["name"] for column in inspector.get_columns("signal")}
    migrations = {
        "screen_scope": "ALTER TABLE signal ADD COLUMN screen_scope VARCHAR",
        "screen_label": "ALTER TABLE signal ADD COLUMN screen_label VARCHAR",
        "universe": "ALTER TABLE signal ADD COLUMN universe VARCHAR",
        "watchlist_member": "ALTER TABLE signal ADD COLUMN watchlist_member BOOLEAN DEFAULT 0",
    }

    with engine.begin() as conn:
        for column, ddl in migrations.items():
            if column not in existing:
                conn.execute(text(ddl))


def _ensure_account_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    account_migrations = {
        "portfoliostate": {
            "account_name": "ALTER TABLE portfoliostate ADD COLUMN account_name VARCHAR DEFAULT 'default'",
        },
        "position": {
            "account_name": "ALTER TABLE position ADD COLUMN account_name VARCHAR DEFAULT 'default'",
        },
        "trade": {
            "account_name": "ALTER TABLE trade ADD COLUMN account_name VARCHAR DEFAULT 'default'",
        },
        "pipelinerun": {
            "account_name": "ALTER TABLE pipelinerun ADD COLUMN account_name VARCHAR DEFAULT 'siriv5'",
        },
        "tradeproposal": {
            "account_name": "ALTER TABLE tradeproposal ADD COLUMN account_name VARCHAR DEFAULT 'siriv5'",
        },
    }

    with engine.begin() as conn:
        for table, migrations in account_migrations.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, ddl in migrations.items():
                if column not in existing:
                    conn.execute(text(ddl))


def _ensure_autonomy_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    migrations = {
        "tradeplan": {
            "linked_position_qty": "ALTER TABLE tradeplan ADD COLUMN linked_position_qty FLOAT",
            "lifecycle_stage": "ALTER TABLE tradeplan ADD COLUMN lifecycle_stage VARCHAR DEFAULT 'new'",
            "parent_trade_plan_id": "ALTER TABLE tradeplan ADD COLUMN parent_trade_plan_id INTEGER",
            "last_reviewed_at": "ALTER TABLE tradeplan ADD COLUMN last_reviewed_at DATETIME",
        },
    }

    with engine.begin() as conn:
        for table, table_migrations in migrations.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, ddl in table_migrations.items():
                if column not in existing:
                    conn.execute(text(ddl))


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _ensure_signal_columns()
    _ensure_account_columns()
    _ensure_autonomy_columns()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
