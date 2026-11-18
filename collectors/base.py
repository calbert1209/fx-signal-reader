"""
Base Collector Module for USD/JPY Signal Reader.
Provides core database initialization, connection handling (WAL mode),
and exponential backoff retry logic with jitter for network requests and rate limits.
"""

from __future__ import annotations

import logging
import os
import random
import sqlite3
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BaseCollector")


class BaseCollector(ABC):
    """
    Abstract base class for all signal data collectors.
    """

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        db_path: Path | str = "usdjpy_signals.db",
        schema_path: Path | str = "schema.sql",
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        jitter: bool = True,
        timeout: float = 12.0,
    ) -> None:
        self.db_path = Path(db_path)
        self.schema_path = Path(schema_path)
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.jitter = jitter
        self.timeout = timeout
        self.logger = logging.getLogger(self.__class__.__name__)

    def get_connection(self) -> sqlite3.Connection:
        """
        Creates and returns a SQLite connection configured in WAL mode.
        Ensures foreign keys are enabled and initializes schema if tables are missing.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row

        # Initialize schema if schema file exists
        if self.schema_path.exists():
            schema_sql = self.schema_path.read_text(encoding="utf-8")
            conn.executescript(schema_sql)
            conn.commit()

        return conn

    def execute_with_retry(
        self,
        fn: Callable[..., Any],
        *args: Any,
        retry_exceptions: tuple[type[Exception], ...] = (
            requests.exceptions.RequestException,
            ConnectionError,
            TimeoutError,
        ),
        retry_on_429: bool = True,
        **kwargs: Any,
    ) -> Any:
        """
        Executes a callable with exponential backoff and jitter.
        Retries on specified exceptions and HTTP 429 / 5xx responses.
        """
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                res = fn(*args, **kwargs)
                if isinstance(res, requests.Response):
                    # Check for rate-limiting (429) or server errors (5xx)
                    if res.status_code == 429:
                        if not retry_on_429:
                            return res
                        retry_after = res.headers.get("Retry-After")
                        delay = self.calculate_backoff_delay(attempt, retry_after)
                        if attempt < self.max_retries:
                            self.logger.warning(
                                f"HTTP 429 received. Retrying in {delay:.2f}s "
                                f"(attempt {attempt + 1}/{self.max_retries})..."
                            )
                            time.sleep(delay)
                            continue
                        else:
                            return res
                    elif 500 <= res.status_code < 600:
                        delay = self.calculate_backoff_delay(attempt)
                        if attempt < self.max_retries:
                            self.logger.warning(
                                f"HTTP {res.status_code} received. Retrying in {delay:.2f}s "
                                f"(attempt {attempt + 1}/{self.max_retries})..."
                            )
                            time.sleep(delay)
                            continue
                        else:
                            res.raise_for_status()
                return res
            except retry_exceptions as exc:
                last_exception = exc
                if attempt < self.max_retries:
                    delay = self.calculate_backoff_delay(attempt)
                    self.logger.warning(
                        f"Request failed ({exc}). Retrying in {delay:.2f}s "
                        f"(attempt {attempt + 1}/{self.max_retries})..."
                    )
                    time.sleep(delay)
                else:
                    self.logger.error(
                        f"Max retries ({self.max_retries}) exceeded for {fn.__name__}: {exc}"
                    )
                    raise

        if last_exception:
            raise last_exception

    def calculate_backoff_delay(
        self, attempt: int, retry_after_header: Optional[str] = None
    ) -> float:
        """
        Calculates exponential backoff delay with optional jitter and Retry-After support.
        """
        if retry_after_header and retry_after_header.strip().isdigit():
            retry_after_sec = float(retry_after_header.strip())
            # Cap extreme Retry-After values to a sensible limit (e.g. 60s)
            return min(max(retry_after_sec, 1.0), 60.0)

        delay = self.backoff_factor * (2**attempt)
        if self.jitter:
            delay += random.uniform(0.1, 0.5 * (attempt + 1))
        return delay

    def fetch_url(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
        retry_on_429: bool = True,
    ) -> requests.Response:
        """
        Fetches an HTTP/HTTPS URL with built-in retry and default headers.
        """
        req_headers = {"User-Agent": self.DEFAULT_USER_AGENT}
        if headers:
            req_headers.update(headers)

        req_timeout = timeout or self.timeout

        def _do_get() -> requests.Response:
            resp = requests.get(
                url, headers=req_headers, params=params, timeout=req_timeout
            )
            if encoding:
                resp.encoding = encoding
            return resp

        return self.execute_with_retry(_do_get, retry_on_429=retry_on_429)

    @abstractmethod
    def collect(self) -> Dict[str, Any]:
        """
        Executes the collection routine and writes results to the database.
        Returns a dictionary of execution metrics.
        """
        raise NotImplementedError
