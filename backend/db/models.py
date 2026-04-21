"""SQLAlchemy models for persistence.

MVP stores scanner hits, setup signals, and trade lifecycle events so you
can review the day later. Not wired into routes yet — can be turned on
by creating the engine/session in phase 2.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Column,
    DateTime,
    Date,
    Float,
    Integer,
    String,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class ScanResult(Base):
    __tablename__ = "scan_results"
    id = Column(Integer, primary_key=True)
    symbol = Column(String(16), nullable=False, index=True)
    trading_day = Column(Date, nullable=False, index=True)
    price = Column(Float, nullable=False)
    gap_pct = Column(Float, nullable=False)
    relative_volume = Column(Float, nullable=False)
    premarket_volume = Column(Integer, nullable=False)
    avg_dollar_volume = Column(Float, nullable=False)
    atr_14d = Column(Float, nullable=False)
    score = Column(Float, nullable=False)
    reasons = Column(JSON, nullable=False)
    side_bias = Column(String(8), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SetupEvent(Base):
    __tablename__ = "setup_events"
    id = Column(Integer, primary_key=True)
    symbol = Column(String(16), nullable=False, index=True)
    trading_day = Column(Date, nullable=False, index=True)
    setup = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)
    entry = Column(Float, nullable=False)
    stop = Column(Float, nullable=False)
    target1 = Column(Float, nullable=False)
    risk_per_share = Column(Float, nullable=False)
    score = Column(Float, nullable=False)
    reasons = Column(JSON, nullable=False)
    triggered_at = Column(DateTime, nullable=False)


class TradeRecord(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True)
    symbol = Column(String(16), nullable=False, index=True)
    trading_day = Column(Date, nullable=False, index=True)
    side = Column(String(8), nullable=False)
    entry = Column(Float, nullable=False)
    stop = Column(Float, nullable=False)
    shares = Column(Integer, nullable=False)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    opened_at = Column(DateTime, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    setup = Column(String(32), nullable=True)
    notes = Column(String(512), nullable=True)
