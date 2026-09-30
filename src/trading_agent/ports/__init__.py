"""Ports = the interfaces the application layer depends on.

Services (engine, discovery, evolution, reporting) import from here, never from `adapters`.
To swap an implementation (another exchange, another notifier, another LLM) write a new
adapter that satisfies the Protocol and wire it in `container.py`. Nothing else changes.
"""

from trading_agent.ports.broker import BrokerPort
from trading_agent.ports.clock import ClockPort
from trading_agent.ports.event_store import EventStorePort
from trading_agent.ports.market_data import MarketDataPort
from trading_agent.ports.notifier import NotifierPort
from trading_agent.ports.researcher import ResearcherPort

__all__ = [
    "BrokerPort",
    "ClockPort",
    "EventStorePort",
    "MarketDataPort",
    "NotifierPort",
    "ResearcherPort",
]
