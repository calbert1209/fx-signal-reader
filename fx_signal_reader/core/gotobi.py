"""Tokyo Gotobi & Nakane fixing intraday session engine.

Models Japanese commercial corporate settlement flows around the Tokyo 9:55 AM JST
(Nakane / 仲値) fix:
1. Gotobi settlement calendar detection (days ending in 5 or 0: 5, 10, 15, 20, 25, 30,
   and final calendar day of each month).
2. Weekend & Japanese bank holiday rollback convention (前倒し - Maedaoshi):
   when a settlement day falls on a non-business day, settlement rolls backward
   to the preceding business day.
3. Intraday fixing windows:
   - Pre-Fix Accumulation Window (08:30-09:55 JST / 23:30-00:55 UTC)
   - Post-Fix Exhaustion Window (09:55-11:00 JST / 00:55-02:00 UTC)
   - Standard Window: outside fixing hours.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

# Tokyo Standard Time (JST is fixed UTC+9 with no daylight saving time)
JST = timezone(timedelta(hours=9), name="JST")

# Intraday fixing window boundary times (JST)
PRE_FIX_START = time(8, 30)
PRE_FIX_END = time(9, 55)
POST_FIX_START = time(9, 55)
POST_FIX_END = time(11, 0)

# Session phase identifiers
PHASE_PRE_FIX = "PRE_FIX"
PHASE_POST_FIX = "POST_FIX"
PHASE_STANDARD = "STANDARD"

# Base Gotobi day-of-month target numbers
GOTOBI_BASE_DAYS = (5, 10, 15, 20, 25, 30)

# Module-level cache for holiday sets keyed by calendar year
_HOLIDAY_CACHE: dict[int, set[date]] = {}


def get_vernal_equinox_day(year: int) -> int:
    """Calculate Vernal Equinox Day (春分の日) day-of-month in JST for 1980-2099."""
    return int(20.8431 + 0.242194 * (year - 1980) - int((year - 1980) / 4))


def get_autumnal_equinox_day(year: int) -> int:
    """Calculate Autumnal Equinox Day (秋分の日) day-of-month in JST for 1980-2099."""
    return int(23.2488 + 0.242194 * (year - 1980) - int((year - 1980) / 4))


def get_nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Return the date of the nth occurrence of weekday (0=Mon .. 6=Sun) in the given month."""
    first_day = date(year, month, 1)
    day = 1 + (weekday - first_day.weekday()) % 7 + (n - 1) * 7
    return date(year, month, day)


def get_japanese_holidays(year: int) -> set[date]:
    """Return all Japanese national public holidays and bank holidays for a given year.
    
    Includes:
    - Official Japanese national holidays (国民の祝日に関する法律).
    - Happy Monday floating holidays (成人の日, 海の日, 敬老の日, スポーツの日).
    - Vernal and Autumnal equinox days (春分の日, 秋分の日).
    - Citizen's holiday (国民の休日) when a day is sandwiched between two national holidays.
    - Substitute holidays (振替休日) when a national holiday falls on Sunday.
    - Japanese Banking Act bank holidays (銀行法第15条: Dec 31, Jan 2, Jan 3).
    - Special Tokyo Olympics holiday shifts (2020, 2021) and Imperial transitions (2019).
    """
    if year in _HOLIDAY_CACHE:
        return _HOLIDAY_CACHE[year]

    holidays: set[date] = set()
    national_holidays: set[date] = set()

    # 1. New Year's Day (元日)
    national_holidays.add(date(year, 1, 1))

    # 2. Coming of Age Day (成人の日 - 2nd Monday of January)
    national_holidays.add(get_nth_weekday_of_month(year, 1, 0, 2))

    # 3. National Foundation Day (建国記念の日 - February 11)
    national_holidays.add(date(year, 2, 11))

    # 4. Emperor's Birthday (天皇誕生日)
    if year >= 2020:
        national_holidays.add(date(year, 2, 23))
    elif year <= 2018:
        national_holidays.add(date(year, 12, 23))

    # 5. Vernal Equinox Day (春分の日)
    national_holidays.add(date(year, 3, get_vernal_equinox_day(year)))

    # 6. Showa Day (昭和の日 - April 29)
    national_holidays.add(date(year, 4, 29))

    # 7. Constitution Memorial Day (憲法記念日 - May 3)
    national_holidays.add(date(year, 5, 3))

    # 8. Greenery Day (みどりの日 - May 4)
    national_holidays.add(date(year, 5, 4))

    # 9. Children's Day (こどもの日 - May 5)
    national_holidays.add(date(year, 5, 5))

    # 10. Marine Day (海の日), Mountain Day (山の日), Sports Day (スポーツの日)
    if year == 2020:
        # Special 2020 Tokyo Olympics dates
        national_holidays.add(date(2020, 7, 23))  # Marine Day
        national_holidays.add(date(2020, 7, 24))  # Sports Day
        national_holidays.add(date(2020, 8, 10))  # Mountain Day
    elif year == 2021:
        # Special 2021 Tokyo Olympics dates
        national_holidays.add(date(2021, 7, 22))  # Marine Day
        national_holidays.add(date(2021, 7, 23))  # Sports Day
        national_holidays.add(date(2021, 8, 8))   # Mountain Day
    else:
        # Marine Day: 3rd Monday of July
        national_holidays.add(get_nth_weekday_of_month(year, 7, 0, 3))
        # Mountain Day: August 11 (inaugurated 2016)
        if year >= 2016:
            national_holidays.add(date(year, 8, 11))
        # Sports Day: 2nd Monday of October
        national_holidays.add(get_nth_weekday_of_month(year, 10, 0, 2))

    # 11. Respect for the Aged Day (敬老の日 - 3rd Monday of September)
    respect_day = get_nth_weekday_of_month(year, 9, 0, 3)
    national_holidays.add(respect_day)

    # 12. Autumnal Equinox Day (秋分の日)
    autumn_day = date(year, 9, get_autumnal_equinox_day(year))
    national_holidays.add(autumn_day)

    # Citizen's Holiday (国民の休日): Silver Week day between Respect for Aged and Autumnal Equinox
    if (autumn_day - respect_day).days == 2:
        national_holidays.add(respect_day + timedelta(days=1))

    # 13. Culture Day (文化の日 - November 3)
    national_holidays.add(date(year, 11, 3))

    # 14. Labor Thanksgiving Day (勤労感謝の日 - November 23)
    national_holidays.add(date(year, 11, 23))

    # 15. Special imperial accession dates in 2019
    if year == 2019:
        national_holidays.add(date(2019, 5, 1))   # Imperial Accession
        national_holidays.add(date(2019, 10, 22))  # Enthronement Ceremony
        national_holidays.add(date(2019, 4, 30))  # Sandwiched Citizen's Holiday
        national_holidays.add(date(2019, 5, 2))   # Sandwiched Citizen's Holiday

    holidays.update(national_holidays)

    # Substitute holidays (振替休日):
    # When a national holiday falls on Sunday, the next non-holiday business day becomes a holiday.
    for h in sorted(national_holidays):
        if h.weekday() == 6:  # Sunday
            curr = h + timedelta(days=1)
            while curr in holidays:
                curr += timedelta(days=1)
            holidays.add(curr)

    # Japanese Banking Act bank holidays (銀行法第15条)
    holidays.add(date(year, 1, 2))
    holidays.add(date(year, 1, 3))
    holidays.add(date(year, 12, 31))

    _HOLIDAY_CACHE[year] = holidays
    return holidays


def is_japanese_holiday(dt: date) -> bool:
    """Return True if the date is a Japanese national holiday or bank holiday."""
    return dt in get_japanese_holidays(dt.year)


def is_japanese_business_day(date_jst: date) -> bool:
    """Check if date_jst is a Japanese banking/financial business day (Mon-Fri, excluding holidays)."""
    if date_jst.weekday() >= 5:  # Saturday or Sunday
        return False
    return not is_japanese_holiday(date_jst)


def get_preceding_business_day(target_date: date) -> date:
    """Roll a date backward to the preceding Japanese business day if it falls on a weekend or holiday."""
    curr = target_date
    while not is_japanese_business_day(curr):
        curr -= timedelta(days=1)
    return curr


def get_target_gotobi_days(year: int, month: int) -> list[int]:
    """Return target Gotobi calendar days for a given month: 5, 10, 15, 20, 25, 30, and month-end."""
    _, last_day = calendar.monthrange(year, month)
    days = [5, 10, 15, 20, 25]
    if last_day >= 30:
        days.append(30)
    if last_day not in days:
        days.append(last_day)
    return sorted(days)


def get_gotobi_dates_for_month(year: int, month: int) -> list[date]:
    """Return unique, sorted effective Gotobi settlement dates for the target Gotobi days in year/month.
    
    Each target Gotobi day (5, 10, 15, 20, 25, 30, and month-end) rolls backward
    to the preceding Japanese business day if it falls on a weekend or holiday.
    """
    targets = get_target_gotobi_days(year, month)
    effective_dates: set[date] = set()
    for day in targets:
        target_dt = date(year, month, day)
        effective_dates.add(get_preceding_business_day(target_dt))
    return sorted(effective_dates)


def is_gotobi_day(date_jst: date) -> bool:
    """Determine whether date_jst is an effective Gotobi settlement day.
    
    Returns True if date_jst is a Japanese business day and corresponds to an effective
    Gotobi settlement day (either naturally or through backward rollback from a weekend/holiday).
    """
    if not is_japanese_business_day(date_jst):
        return False

    # Check effective dates belonging to this month
    if date_jst in get_gotobi_dates_for_month(date_jst.year, date_jst.month):
        return True

    # Check if a target from the subsequent month rolled back across month boundary
    next_year, next_month = (date_jst.year + 1, 1) if date_jst.month == 12 else (date_jst.year, date_jst.month + 1)
    if date_jst in get_gotobi_dates_for_month(next_year, next_month):
        return True

    return False


def get_target_gotobi_for_date(date_jst: date) -> date | None:
    """Identify the original target Gotobi date that rolled into date_jst, if applicable."""
    if not is_gotobi_day(date_jst):
        return None

    # Search current month and subsequent month
    search_months = [(date_jst.year, date_jst.month)]
    next_year, next_month = (date_jst.year + 1, 1) if date_jst.month == 12 else (date_jst.year, date_jst.month + 1)
    search_months.append((next_year, next_month))

    candidates: list[date] = []
    for y, m in search_months:
        for day in get_target_gotobi_days(y, m):
            target_dt = date(y, m, day)
            if get_preceding_business_day(target_dt) == date_jst:
                candidates.append(target_dt)

    return min(candidates) if candidates else None


@dataclass(frozen=True)
class GotobiSessionInfo:
    """Structured evaluation of Tokyo Gotobi & Nakane fixing intraday status."""

    is_gotobi_day: bool
    session_phase: str  # "PRE_FIX", "POST_FIX", or "STANDARD"
    is_pre_fix_window: bool
    is_post_fix_window: bool
    description: str
    target_gotobi_date: date | None = None
    effective_gotobi_date: date | None = None
    is_business_day: bool = True
    timestamp_utc: str = ""
    jst_datetime: str = ""

    @property
    def is_gotobi_pre_fix(self) -> bool:
        """True if currently in Pre-Fix accumulation window on an active Gotobi day."""
        return self.is_gotobi_day and self.is_pre_fix_window

    @property
    def is_gotobi_post_fix(self) -> bool:
        """True if currently in Post-Fix exhaustion window on an active Gotobi day."""
        return self.is_gotobi_day and self.is_post_fix_window

    def to_dict(self) -> dict[str, Any]:
        """Convert session info to a JSON-serializable dictionary."""
        return {
            "is_gotobi_day": self.is_gotobi_day,
            "session_phase": self.session_phase,
            "is_pre_fix_window": self.is_pre_fix_window,
            "is_post_fix_window": self.is_post_fix_window,
            "is_gotobi_pre_fix": self.is_gotobi_pre_fix,
            "is_gotobi_post_fix": self.is_gotobi_post_fix,
            "is_business_day": self.is_business_day,
            "target_gotobi_date": self.target_gotobi_date.isoformat() if self.target_gotobi_date else None,
            "effective_gotobi_date": self.effective_gotobi_date.isoformat() if self.effective_gotobi_date else None,
            "timestamp_utc": self.timestamp_utc,
            "jst_datetime": self.jst_datetime,
            "description": self.description,
        }


def _parse_utc_datetime(ts: datetime | str | None) -> datetime:
    """Normalize input timestamp into timezone-aware UTC datetime."""
    if ts is None:
        return datetime.now(timezone.utc)
    if isinstance(ts, str):
        # Support ISO 8601 strings (e.g., '2024-05-10T00:15:00Z', '2024-05-10 00:15:00')
        cleaned = ts.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    raise TypeError(f"Expected datetime, ISO str, or None; got {type(ts).__name__}")


def evaluate_gotobi_session(timestamp_utc: datetime | str | None = None) -> GotobiSessionInfo:
    """Evaluate Tokyo Gotobi & Nakane fixing intraday status for a given timestamp.
    
    Args:
        timestamp_utc: UTC datetime object, ISO timestamp string, or None (defaults to current time).
        
    Returns:
        GotobiSessionInfo containing Gotobi day status, session phase, window flags, and notes.
    """
    utc_dt = _parse_utc_datetime(timestamp_utc)
    jst_dt = utc_dt.astimezone(JST)

    date_jst = jst_dt.date()
    time_jst = jst_dt.time()

    is_bday = is_japanese_business_day(date_jst)
    is_gotobi = is_gotobi_day(date_jst)
    target_date = get_target_gotobi_for_date(date_jst) if is_gotobi else None

    # Evaluate intraday session window
    if PRE_FIX_START <= time_jst < PRE_FIX_END:
        session_phase = PHASE_PRE_FIX
        is_pre_fix = True
        is_post_fix = False
    elif POST_FIX_START <= time_jst < POST_FIX_END:
        session_phase = PHASE_POST_FIX
        is_pre_fix = False
        is_post_fix = True
    else:
        session_phase = PHASE_STANDARD
        is_pre_fix = False
        is_post_fix = False

    # Construct descriptive narrative
    notes_parts: list[str] = []
    if is_gotobi:
        target_info = f" (target {target_date.strftime('%Y-%m-%d')})" if target_date and target_date != date_jst else ""
        if session_phase == PHASE_PRE_FIX:
            notes_parts.append(
                f"Gotobi Pre-Fix accumulation window{target_info}: commercial USD buying flow / upward drift expected."
            )
        elif session_phase == PHASE_POST_FIX:
            notes_parts.append(
                f"Gotobi Post-Fix exhaustion window{target_info}: fixing demand concluded; upside momentum dissipates."
            )
        else:
            notes_parts.append(f"Tokyo Gotobi settlement day{target_info} outside fixing window.")
    else:
        if not is_bday:
            notes_parts.append("Tokyo market holiday / non-business day (banks closed).")
        elif session_phase == PHASE_PRE_FIX:
            notes_parts.append("Standard Tokyo Pre-Fix window (non-Gotobi day).")
        elif session_phase == PHASE_POST_FIX:
            notes_parts.append("Standard Tokyo Post-Fix window (non-Gotobi day).")
        else:
            notes_parts.append("Standard trading session outside Tokyo fixing window.")

    description = " ".join(notes_parts)

    return GotobiSessionInfo(
        is_gotobi_day=is_gotobi,
        session_phase=session_phase,
        is_pre_fix_window=is_pre_fix,
        is_post_fix_window=is_post_fix,
        description=description,
        target_gotobi_date=target_date,
        effective_gotobi_date=date_jst if is_gotobi else None,
        is_business_day=is_bday,
        timestamp_utc=utc_dt.isoformat(),
        jst_datetime=jst_dt.isoformat(),
    )
