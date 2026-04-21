"""In-process event bus.

Every module either emits or consumes one of a small set of event types.
Keeping the bus dependency-free lets us swap in Redis streams later without
touching producers/consumers.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    SCANNER_HIT = "scanner_hit"
    SETUP_SIGNAL = "setup_signal"
    REGIME_CHANGE = "regime_change"
    TRADE_OPEN = "trade_open"
    TRADE_CLOSE = "trade_close"
    WATCHLIST_UPDATE = "watchlist_update"
    NEWS_HEADLINE = "news_headline"


@dataclass
class Event:
    type: EventType
    payload: dict[str, Any]
    ts: datetime


Handler = Callable[[Event], None]


class EventBus:
    """Synchronous pub/sub. Handlers run inline; keep them fast."""

    def __init__(self) -> None:
        self._subs: dict[EventType, list[Handler]] = defaultdict(list)

    def subscribe(self, type_: EventType, handler: Handler) -> None:
        self._subs[type_].append(handler)

    def publish(self, type_: EventType, payload: dict[str, Any]) -> None:
        event = Event(type=type_, payload=payload, ts=datetime.now(timezone.utc))
        for handler in self._subs.get(type_, ()):
            handler(event)


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
