"""
Modular collector package for USD/JPY Signal Reader.
"""

from fx_signal_reader.collectors.base import BaseCollector
from fx_signal_reader.collectors.calendar import CalendarCollector
from fx_signal_reader.collectors.market_ticks import MarketTicksCollector
from fx_signal_reader.collectors.news_rss import NewsRSSCollector

__all__ = [
    "BaseCollector",
    "MarketTicksCollector",
    "CalendarCollector",
    "NewsRSSCollector",
]
