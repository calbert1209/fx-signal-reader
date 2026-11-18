"""
Modular collector package for USD/JPY Signal Reader.
"""

from collectors.base import BaseCollector
from collectors.calendar import CalendarCollector
from collectors.market_ticks import MarketTicksCollector
from collectors.news_rss import NewsRSSCollector

__all__ = [
    "BaseCollector",
    "MarketTicksCollector",
    "CalendarCollector",
    "NewsRSSCollector",
]
