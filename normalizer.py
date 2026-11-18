"""
USD/JPY Signal Reader - Shared Normalization Module
Task P2-03: Normalization & Spread Calculation

Provides standardized UTC timestamp parsing and deterministic deduplication
hashing for the USD/JPY signal pipeline.
"""

from datetime import date, datetime, timezone
import hashlib
import re
from typing import Any, Optional, Union
from dateutil import parser as date_parser


# Strict ISO-8601 UTC regex pattern: YYYY-MM-DDTHH:MM:SSZ
ISO8601_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Japanese Era pattern (e.g., R8.11.17, R06.05.01, H30.12.31, S60.1.1)
JAPANESE_ERA_PATTERN = re.compile(r"^([rRhHsS])(\d{1,2})\.(\d{1,2})\.(\d{1,2})$")

# Forex Factory 12-hour time pattern (e.g., 11:50pm, 2:00am, 12:00pm)
FF_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})\s*([apAP][mM])$")


def is_valid_iso8601_utc(timestamp_str: str) -> bool:
    """
    Validates if a string strictly conforms to ISO-8601 UTC format (YYYY-MM-DDTHH:MM:SSZ)
    and represents a valid calendar datetime.
    """
    if not isinstance(timestamp_str, str):
        return False
    if not ISO8601_UTC_PATTERN.match(timestamp_str):
        return False
    try:
        datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ")
        return True
    except ValueError:
        return False


def parse_japanese_era_date(era_str: str) -> str:
    """
    Parses Japanese Imperial Era date strings commonly used in Japan Ministry of Finance (MoF)
    releases into ISO-8601 calendar date string (YYYY-MM-DD).

    Supported eras:
      - R / r: Reiwa (R1 = 2019, R6 = 2024, R8 = 2026)
      - H / h: Heisei (H1 = 1989, H31 = 2019)
      - S / s: Showa (S1 = 1926, S64 = 1989)

    Example:
      'R8.11.17' -> '2026-11-17'
      'R6.5.1'   -> '2024-05-01'
    """
    m = JAPANESE_ERA_PATTERN.match(era_str.strip())
    if not m:
        raise ValueError(f"Invalid Japanese Era date string: {era_str}")

    era_char = m.group(1).upper()
    era_year = int(m.group(2))
    month = int(m.group(3))
    day = int(m.group(4))

    if era_char == "R":
        gregorian_year = 2018 + era_year
    elif era_char == "H":
        gregorian_year = 1988 + era_year
    elif era_char == "S":
        gregorian_year = 1925 + era_year
    else:
        raise ValueError(f"Unsupported era prefix: {era_char}")

    # Validate valid date
    d = date(gregorian_year, month, day)
    return d.isoformat()


def normalize_datetime_utc(
    value: Union[datetime, date, str, int, float, None] = None,
    default_timezone: timezone = timezone.utc,
    fallback_now: bool = False,
) -> str:
    """
    Converts various date, datetime, string, or epoch timestamp representations
    into a standardized ISO-8601 UTC string (`YYYY-MM-DDTHH:MM:SSZ`).

    Args:
        value: Datetime, date, epoch number, or formatted date string.
        default_timezone: Timezone to assume if a string/datetime is timezone-naive (defaults to UTC).
        fallback_now: If True, returns current UTC timestamp on empty or invalid inputs;
                      if False, raises ValueError.

    Returns:
        Standardized ISO-8601 UTC string in `YYYY-MM-DDTHH:MM:SSZ` format.
    """
    if value is None:
        if fallback_now:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        raise ValueError("Cannot normalize None timestamp when fallback_now is False.")

    # 1. Numeric Epoch Timestamps (seconds or milliseconds)
    if isinstance(value, (int, float)):
        try:
            val_float = float(value)
            # If timestamp exceeds 1e11 (~year 5138 in seconds), assume milliseconds
            if val_float > 1e11:
                val_float /= 1000.0
            dt = datetime.fromtimestamp(val_float, tz=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as exc:
            if fallback_now:
                return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            raise ValueError(f"Failed to parse numeric epoch timestamp {value}: {exc}") from exc

    # 2. Python datetime object (must check before date since datetime subclasses date)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            dt = value.replace(tzinfo=default_timezone)
        else:
            dt = value
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 3. Python date object
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00Z"

    # 4. String representations
    if isinstance(value, str):
        clean_str = value.strip()
        if not clean_str:
            if fallback_now:
                return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            raise ValueError("Cannot normalize empty timestamp string.")

        # If already matching strict ISO-8601 UTC format, return as is
        if ISO8601_UTC_PATTERN.match(clean_str):
            # Verify validity of calendar components
            datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%SZ")
            return clean_str

        # Check Japanese Era pattern (e.g., R8.11.17)
        if JAPANESE_ERA_PATTERN.match(clean_str):
            iso_date = parse_japanese_era_date(clean_str)
            return f"{iso_date}T00:00:00Z"

        # General dateutil parsing
        try:
            parsed = date_parser.parse(clean_str)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=default_timezone)
            utc_dt = parsed.astimezone(timezone.utc)
            return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as exc:
            # Fallback regex for pure YYYY-MM-DD
            m = re.match(r"^(\d{4}-\d{2}-\d{2})", clean_str)
            if m:
                try:
                    datetime.strptime(m.group(1), "%Y-%m-%d")
                    return f"{m.group(1)}T00:00:00Z"
                except ValueError:
                    pass

            if fallback_now:
                return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            raise ValueError(f"Unable to parse timestamp string '{value}': {exc}") from exc

    if fallback_now:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raise ValueError(f"Unsupported timestamp type: {type(value).__name__}")


def parse_event_datetime(
    date_str: str,
    time_str: Optional[str] = None,
    default_timezone: timezone = timezone.utc,
) -> str:
    """
    Combines and standardizes date and time strings commonly found in economic calendars
    (e.g., Forex Factory '11-15-2026' and '11:50pm' / 'All Day' / 'Tentative') into ISO-8601 UTC.
    """
    clean_date = date_str.strip()
    clean_time = time_str.strip() if time_str else ""

    # If time is missing or indicates non-specific intraday timing
    if (
        not clean_time
        or "day" in clean_time.lower()
        or "tentative" in clean_time.lower()
    ):
        return normalize_datetime_utc(clean_date, default_timezone=default_timezone)

    combined_str = f"{clean_date} {clean_time}"
    return normalize_datetime_utc(combined_str, default_timezone=default_timezone)


def generate_url_hash(url: str) -> str:
    """
    Generates a deterministic 64-character SHA-256 hexadecimal hash for news article URLs.
    Used for primary key deduplication in `news_headlines`.

    Args:
        url: The web URL of the article.

    Returns:
        64-character lowercase hex string.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string to generate url_hash.")

    clean_url = url.strip()
    return hashlib.sha256(clean_url.encode("utf-8")).hexdigest().lower()


def generate_event_hash(timestamp_utc: str, currency: str, title: str) -> str:
    """
    Generates a deterministic 32-character SHA-256 hexadecimal hash for macro calendar events.
    Used for primary key deduplication in `macro_events`.

    Payload format: `{timestamp_utc}:{CURRENCY}:{title}`

    Args:
        timestamp_utc: Event timestamp (standardized or converted to ISO-8601 UTC).
        currency: 3-letter currency code (e.g., USD, JPY).
        title: Title of the macroeconomic release.

    Returns:
        32-character lowercase hex string (first 32 chars of SHA-256 digest).
    """
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("Currency must be a non-empty string to generate event_hash.")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Title must be a non-empty string to generate event_hash.")

    # Ensure timestamp is normalized ISO-8601 UTC
    norm_ts = normalize_datetime_utc(timestamp_utc)
    clean_currency = currency.strip().upper()
    clean_title = title.strip()

    payload = f"{norm_ts}:{clean_currency}:{clean_title}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32].lower()


def clean_text(text: Optional[str]) -> Optional[str]:
    """
    Cleans text by stripping whitespace and collapsing multiple spaces into a single space.
    Returns None if text is empty or None.
    """
    if text is None:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned if cleaned else None


def parse_float_or_none(val: Any) -> Optional[float]:
    """
    Parses a numeric or string value to float, handling percentage signs,
    thousands commas, and missing indicators like '.' or '-'.
    Returns None if value cannot be parsed.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        import math
        return float(val) if not math.isnan(val) else None

    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned or cleaned in (".", "-", "N/A", "nan", "None"):
            return None
        cleaned = cleaned.replace("%", "").replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None
