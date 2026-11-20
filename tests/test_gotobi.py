"""Comprehensive unit tests for Tokyo Gotobi & Nakane fixing intraday session engine."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest

from fx_signal_reader.core.gotobi import (
    GOTOBI_BASE_DAYS,
    JST,
    PHASE_POST_FIX,
    PHASE_PRE_FIX,
    PHASE_STANDARD,
    POST_FIX_END,
    POST_FIX_START,
    PRE_FIX_END,
    PRE_FIX_START,
    GotobiSessionInfo,
    evaluate_gotobi_session,
    get_autumnal_equinox_day,
    get_gotobi_dates_for_month,
    get_japanese_holidays,
    get_preceding_business_day,
    get_target_gotobi_days,
    get_target_gotobi_for_date,
    get_vernal_equinox_day,
    is_gotobi_day,
    is_japanese_business_day,
    is_japanese_holiday,
)


class TestJapaneseCalendarAndHolidays:
    """Tests for Japanese public holidays, bank holidays, and business days."""

    def test_bank_holidays(self) -> None:
        """Jan 2, Jan 3, and Dec 31 must always be non-business bank holidays."""
        for year in [2022, 2023, 2024, 2025]:
            assert not is_japanese_business_day(datetime.date(year, 1, 2))
            assert not is_japanese_business_day(datetime.date(year, 1, 3))
            assert not is_japanese_business_day(datetime.date(year, 12, 31))

    def test_fixed_national_holidays(self) -> None:
        """Verify fixed-date national holidays."""
        # New Year's Day
        assert is_japanese_holiday(datetime.date(2024, 1, 1))
        # National Foundation Day
        assert is_japanese_holiday(datetime.date(2024, 2, 11))
        # Emperor's Birthday (post-2020: Feb 23)
        assert is_japanese_holiday(datetime.date(2024, 2, 23))
        # Showa Day
        assert is_japanese_holiday(datetime.date(2024, 4, 29))
        # Constitution Memorial Day
        assert is_japanese_holiday(datetime.date(2024, 5, 3))
        # Greenery Day
        assert is_japanese_holiday(datetime.date(2024, 5, 4))
        # Children's Day
        assert is_japanese_holiday(datetime.date(2024, 5, 5))
        # Mountain Day (Aug 11 in standard years)
        assert is_japanese_holiday(datetime.date(2024, 8, 11))
        # Culture Day
        assert is_japanese_holiday(datetime.date(2024, 11, 3))
        # Labor Thanksgiving Day
        assert is_japanese_holiday(datetime.date(2024, 11, 23))

    def test_happy_monday_holidays(self) -> None:
        """Verify Happy Monday floating holidays."""
        # 2024 Coming of Age Day (2nd Monday Jan): Jan 8
        assert is_japanese_holiday(datetime.date(2024, 1, 8))
        assert datetime.date(2024, 1, 8).weekday() == 0

        # 2024 Marine Day (3rd Monday Jul): Jul 15
        assert is_japanese_holiday(datetime.date(2024, 7, 15))
        assert datetime.date(2024, 7, 15).weekday() == 0

        # 2024 Respect for the Aged Day (3rd Monday Sep): Sep 16
        assert is_japanese_holiday(datetime.date(2024, 9, 16))
        assert datetime.date(2024, 9, 16).weekday() == 0

        # 2024 Sports Day (2nd Monday Oct): Oct 14
        assert is_japanese_holiday(datetime.date(2024, 10, 14))
        assert datetime.date(2024, 10, 14).weekday() == 0

    def test_substitute_holidays(self) -> None:
        """When a national holiday falls on Sunday, next non-holiday is a substitute holiday."""
        # In 2024:
        # Feb 11 (Sun) -> Feb 12 (Mon) is substitute
        assert is_japanese_holiday(datetime.date(2024, 2, 12))
        assert not is_japanese_business_day(datetime.date(2024, 2, 12))

        # May 5 (Sun) -> May 6 (Mon) is substitute
        assert is_japanese_holiday(datetime.date(2024, 5, 6))
        assert not is_japanese_business_day(datetime.date(2024, 5, 6))

        # Aug 11 (Sun) -> Aug 12 (Mon) is substitute
        assert is_japanese_holiday(datetime.date(2024, 8, 12))
        assert not is_japanese_business_day(datetime.date(2024, 8, 12))

        # Sep 22 (Sun) -> Sep 23 (Mon) is substitute
        assert is_japanese_holiday(datetime.date(2024, 9, 23))
        assert not is_japanese_business_day(datetime.date(2024, 9, 23))

        # Nov 3 (Sun) -> Nov 4 (Mon) is substitute
        assert is_japanese_holiday(datetime.date(2024, 11, 4))
        assert not is_japanese_business_day(datetime.date(2024, 11, 4))

    def test_equinox_calculations(self) -> None:
        """Equinox day calculations match astronomical records."""
        # 2020-2026 Vernal Equinox
        assert get_vernal_equinox_day(2020) == 20
        assert get_vernal_equinox_day(2021) == 20
        assert get_vernal_equinox_day(2022) == 21
        assert get_vernal_equinox_day(2023) == 21
        assert get_vernal_equinox_day(2024) == 20
        assert get_vernal_equinox_day(2025) == 20
        assert get_vernal_equinox_day(2026) == 20

        # Autumnal Equinox
        assert get_autumnal_equinox_day(2023) == 23
        assert get_autumnal_equinox_day(2024) == 22
        assert get_autumnal_equinox_day(2025) == 23
        assert get_autumnal_equinox_day(2026) == 23

    def test_special_olympics_shifts(self) -> None:
        """Verify 2020 and 2021 Tokyo Olympics special holiday shifts."""
        h2020 = get_japanese_holidays(2020)
        assert datetime.date(2020, 7, 23) in h2020  # Marine Day
        assert datetime.date(2020, 7, 24) in h2020  # Sports Day
        assert datetime.date(2020, 8, 10) in h2020  # Mountain Day

        h2021 = get_japanese_holidays(2021)
        assert datetime.date(2021, 7, 22) in h2021  # Marine Day
        assert datetime.date(2021, 7, 23) in h2021  # Sports Day
        assert datetime.date(2021, 8, 8) in h2021   # Mountain Day

    def test_regular_business_days(self) -> None:
        """Verify normal weekdays are recognized as business days."""
        # A normal Wednesday: 2024-05-15
        assert is_japanese_business_day(datetime.date(2024, 5, 15))
        # A normal Tuesday: 2024-09-10
        assert is_japanese_business_day(datetime.date(2024, 9, 10))
        # Saturday is not a business day
        assert not is_japanese_business_day(datetime.date(2024, 5, 18))
        # Sunday is not a business day
        assert not is_japanese_business_day(datetime.date(2024, 5, 19))


class TestGotobiRollbackConvention:
    """Tests for Gotobi target day identification and weekend/holiday rollbacks."""

    def test_target_days_for_months(self) -> None:
        """Target days include 5, 10, 15, 20, 25, 30 and month-end."""
        # 31-day month
        assert get_target_gotobi_days(2024, 1) == [5, 10, 15, 20, 25, 30, 31]
        # 30-day month
        assert get_target_gotobi_days(2024, 4) == [5, 10, 15, 20, 25, 30]
        # 28-day Feb (standard year)
        assert get_target_gotobi_days(2023, 2) == [5, 10, 15, 20, 25, 28]
        # 29-day Feb (leap year)
        assert get_target_gotobi_days(2024, 2) == [5, 10, 15, 20, 25, 29]

    def test_standard_gotobi_without_rollback(self) -> None:
        """When target Gotobi days fall on business days, no rollback occurs."""
        # 2024-05-10 was Friday (business day)
        assert is_gotobi_day(datetime.date(2024, 5, 10))
        assert get_preceding_business_day(datetime.date(2024, 5, 10)) == datetime.date(2024, 5, 10)

        # 2024-05-15 was Wednesday (business day)
        assert is_gotobi_day(datetime.date(2024, 5, 15))
        assert get_preceding_business_day(datetime.date(2024, 5, 15)) == datetime.date(2024, 5, 15)

        # 2024-05-20 was Monday (business day)
        assert is_gotobi_day(datetime.date(2024, 5, 20))

    def test_saturday_rollback_to_friday(self) -> None:
        """Saturday target rolls back to Friday."""
        # 2024-10-05 was Saturday -> rolls to Friday 2024-10-04
        sat = datetime.date(2024, 10, 5)
        fri = datetime.date(2024, 10, 4)
        assert get_preceding_business_day(sat) == fri
        assert is_gotobi_day(fri)
        assert not is_gotobi_day(sat)  # Saturday itself is never a Gotobi trading day
        assert get_target_gotobi_for_date(fri) == sat

    def test_sunday_rollback_to_friday(self) -> None:
        """Sunday target rolls back to Friday (skipping Saturday)."""
        # 2024-09-15 was Sunday -> rolls to Friday 2024-09-13
        sun = datetime.date(2024, 9, 15)
        fri = datetime.date(2024, 9, 13)
        assert get_preceding_business_day(sun) == fri
        assert is_gotobi_day(fri)
        assert not is_gotobi_day(sun)
        assert get_target_gotobi_for_date(fri) == sun

        # 2024-10-20 was Sunday -> rolls to Friday 2024-10-18
        sun2 = datetime.date(2024, 10, 20)
        fri2 = datetime.date(2024, 10, 18)
        assert get_preceding_business_day(sun2) == fri2
        assert is_gotobi_day(fri2)
        assert get_target_gotobi_for_date(fri2) == sun2

    def test_multi_day_holiday_rollback_golden_week(self) -> None:
        """May 5th during Golden Week rolls back across consecutive holidays."""
        # In May 2024:
        # May 5 (Sun, Children's Day)
        # May 6 (Mon, Substitute Holiday)
        # May 4 (Sat, Greenery Day)
        # May 3 (Fri, Constitution Day)
        # May 2 (Thu, Regular business day)
        # Therefore, May 5 rolls back past 4 and 3 to Thursday May 2!
        target = datetime.date(2024, 5, 5)
        rolled = datetime.date(2024, 5, 2)
        assert get_preceding_business_day(target) == rolled
        assert is_gotobi_day(rolled)
        assert get_target_gotobi_for_date(rolled) == target

    def test_february_month_end_rollbacks(self) -> None:
        """Verify February month-end Gotobi detection in normal and leap years."""
        # 2023 non-leap: Feb 28 was Tuesday -> business day
        assert is_gotobi_day(datetime.date(2023, 2, 28))

        # 2024 leap year: Feb 29 was Thursday -> business day
        assert is_gotobi_day(datetime.date(2024, 2, 29))

        # 2020 leap year: Feb 29 was Saturday -> rolls back to Friday Feb 28
        assert get_preceding_business_day(datetime.date(2020, 2, 29)) == datetime.date(2020, 2, 28)
        assert is_gotobi_day(datetime.date(2020, 2, 28))

    def test_december_year_end_bank_holiday_rollback(self) -> None:
        """Dec 31 is always a bank holiday, so year-end Gotobi rolls to Dec 30 (or earlier)."""
        # In 2024: Dec 31 is Tuesday (bank holiday) -> rolls to Dec 30 (Monday, business day)
        assert get_preceding_business_day(datetime.date(2024, 12, 31)) == datetime.date(2024, 12, 30)
        assert is_gotobi_day(datetime.date(2024, 12, 30))

        # In 2023: Dec 31 was Sunday (bank holiday), Dec 30 was Saturday
        # rolls to Dec 29 (Friday)
        assert get_preceding_business_day(datetime.date(2023, 12, 31)) == datetime.date(2023, 12, 29)
        assert is_gotobi_day(datetime.date(2023, 12, 29))


class TestIntradayFixingSessionWindows:
    """Tests for Pre-Fix, Post-Fix, and Standard intraday fixing window evaluation."""

    def test_pre_fix_boundary_exact_minutes(self) -> None:
        """Evaluate exact boundary minutes for Pre-Fix (08:30:00 to 09:54:59 JST / 23:30:00 to 00:54:59 UTC)."""
        # 1 second before Pre-Fix: 08:29:59 JST (23:29:59 UTC previous day)
        before = evaluate_gotobi_session("2024-05-09T23:29:59Z")
        assert before.session_phase == PHASE_STANDARD
        assert not before.is_pre_fix_window
        assert not before.is_post_fix_window

        # Exact Pre-Fix start: 08:30:00 JST (23:30:00 UTC)
        start = evaluate_gotobi_session("2024-05-09T23:30:00Z")
        assert start.session_phase == PHASE_PRE_FIX
        assert start.is_pre_fix_window
        assert not start.is_post_fix_window
        assert start.is_gotobi_day  # 2024-05-10 is Gotobi day 10
        assert start.is_gotobi_pre_fix

        # Mid Pre-Fix: 09:00:00 JST (00:00:00 UTC)
        mid = evaluate_gotobi_session("2024-05-10T00:00:00Z")
        assert mid.session_phase == PHASE_PRE_FIX
        assert mid.is_pre_fix_window

        # Final second of Pre-Fix: 09:54:59 JST (00:54:59 UTC)
        final_sec = evaluate_gotobi_session("2024-05-10T00:54:59Z")
        assert final_sec.session_phase == PHASE_PRE_FIX
        assert final_sec.is_pre_fix_window
        assert not final_sec.is_post_fix_window

    def test_post_fix_boundary_exact_minutes(self) -> None:
        """Evaluate exact boundary minutes for Post-Fix (09:55:00 to 10:59:59 JST / 00:55:00 to 01:59:59 UTC)."""
        # Exact Fixing Moment / Post-Fix start: 09:55:00 JST (00:55:00 UTC)
        start = evaluate_gotobi_session("2024-05-10T00:55:00Z")
        assert start.session_phase == PHASE_POST_FIX
        assert not start.is_pre_fix_window
        assert start.is_post_fix_window
        assert start.is_gotobi_day
        assert start.is_gotobi_post_fix

        # Mid Post-Fix: 10:15:00 JST (01:15:00 UTC)
        mid = evaluate_gotobi_session("2024-05-10T01:15:00Z")
        assert mid.session_phase == PHASE_POST_FIX
        assert mid.is_post_fix_window

        # Final second of Post-Fix: 10:59:59 JST (01:59:59 UTC)
        final_sec = evaluate_gotobi_session("2024-05-10T01:59:59Z")
        assert final_sec.session_phase == PHASE_POST_FIX
        assert final_sec.is_post_fix_window

        # Exact end / Standard transition: 11:00:00 JST (02:00:00 UTC)
        end = evaluate_gotobi_session("2024-05-10T02:00:00Z")
        assert end.session_phase == PHASE_STANDARD
        assert not end.is_pre_fix_window
        assert not end.is_post_fix_window

    def test_standard_hours(self) -> None:
        """Evaluate general afternoon, evening, and early morning trading hours."""
        # Tokyo afternoon: 14:00 JST (05:00 UTC)
        pm = evaluate_gotobi_session("2024-05-10T05:00:00Z")
        assert pm.session_phase == PHASE_STANDARD
        assert not pm.is_pre_fix_window
        assert not pm.is_post_fix_window
        assert pm.is_gotobi_day  # Still the 10th in JST

        # Tokyo early morning: 06:00 JST (21:00 UTC previous day)
        am = evaluate_gotobi_session("2024-05-09T21:00:00Z")
        assert am.session_phase == PHASE_STANDARD
        assert not am.is_pre_fix_window

    def test_non_gotobi_business_day(self) -> None:
        """On a regular non-Gotobi business day, Pre-Fix and Post-Fix phases still identify fixing hours."""
        # 2024-05-14 was Tuesday (not Gotobi: 10 was Fri, 15 was Wed)
        session = evaluate_gotobi_session("2024-05-14T00:15:00Z")  # 09:15 JST
        assert not session.is_gotobi_day
        assert session.session_phase == PHASE_PRE_FIX
        assert session.is_pre_fix_window
        assert not session.is_gotobi_pre_fix
        assert "Standard Tokyo Pre-Fix window (non-Gotobi day)" in session.description

    def test_weekend_session(self) -> None:
        """On Saturday or Sunday, is_gotobi_day and is_business_day are False."""
        # 2024-05-11 was Saturday
        session = evaluate_gotobi_session("2024-05-11T00:00:00Z")  # 09:00 JST Sat
        assert not session.is_gotobi_day
        assert not session.is_business_day
        assert not session.is_gotobi_pre_fix


class TestEvaluateGotobiSessionInputsAndSerialization:
    """Tests for various input types, serialization, and dataclass behavior."""

    def test_input_variations(self) -> None:
        """Handles datetime objects (naive, UTC, JST) and ISO strings."""
        # 1. UTC-aware datetime
        dt_utc = datetime.datetime(2024, 9, 13, 0, 15, tzinfo=datetime.timezone.utc)
        res1 = evaluate_gotobi_session(dt_utc)
        assert res1.is_gotobi_day  # 2024-09-13 is rolled Gotobi from 15th
        assert res1.is_pre_fix_window

        # 2. Naive datetime (treated as UTC)
        dt_naive = datetime.datetime(2024, 9, 13, 0, 15)
        res2 = evaluate_gotobi_session(dt_naive)
        assert res2.is_gotobi_day
        assert res2.is_pre_fix_window

        # 3. JST-aware datetime (2024-09-13 09:15 JST)
        dt_jst = datetime.datetime(2024, 9, 13, 9, 15, tzinfo=JST)
        res3 = evaluate_gotobi_session(dt_jst)
        assert res3.is_gotobi_day
        assert res3.is_pre_fix_window

        # 4. ISO string with Z
        res4 = evaluate_gotobi_session("2024-09-13T00:15:00Z")
        assert res4.is_gotobi_day
        assert res4.is_pre_fix_window

        # 5. ISO string with space separator
        res5 = evaluate_gotobi_session("2024-09-13 00:15:00")
        assert res5.is_gotobi_day
        assert res5.is_pre_fix_window

        # 6. ISO string with JST offset
        res6 = evaluate_gotobi_session("2024-09-13T09:15:00+09:00")
        assert res6.is_gotobi_day
        assert res6.is_pre_fix_window

        # 7. None (current time should execute without error)
        res7 = evaluate_gotobi_session(None)
        assert isinstance(res7, GotobiSessionInfo)
        assert res7.session_phase in (PHASE_PRE_FIX, PHASE_POST_FIX, PHASE_STANDARD)

    def test_invalid_input_type(self) -> None:
        """Raises TypeError on invalid argument type."""
        with pytest.raises(TypeError):
            evaluate_gotobi_session(12345)  # type: ignore[arg-type]

    def test_to_dict_serialization(self) -> None:
        """to_dict returns expected dictionary structure with JSON-serializable values."""
        res = evaluate_gotobi_session("2024-09-13T00:15:00Z")
        d = res.to_dict()
        assert isinstance(d, dict)
        assert d["is_gotobi_day"] is True
        assert d["session_phase"] == PHASE_PRE_FIX
        assert d["is_pre_fix_window"] is True
        assert d["is_post_fix_window"] is False
        assert d["is_gotobi_pre_fix"] is True
        assert d["is_gotobi_post_fix"] is False
        assert d["is_business_day"] is True
        assert d["target_gotobi_date"] == "2024-09-15"
        assert d["effective_gotobi_date"] == "2024-09-13"
        assert "Gotobi Pre-Fix accumulation window" in d["description"]
        assert "2024-09-13" in d["jst_datetime"]

    def test_target_metadata_tracking(self) -> None:
        """GotobiSessionInfo properly tracks target vs effective dates when rolled."""
        # Sunday Sep 15 rolled to Friday Sep 13
        res = evaluate_gotobi_session("2024-09-13T00:15:00Z")
        assert res.effective_gotobi_date == datetime.date(2024, 9, 13)
        assert res.target_gotobi_date == datetime.date(2024, 9, 15)

        # Standard non-rolled Gotobi day: 2024-05-10
        res2 = evaluate_gotobi_session("2024-05-10T00:15:00Z")
        assert res2.effective_gotobi_date == datetime.date(2024, 5, 10)
        assert res2.target_gotobi_date == datetime.date(2024, 5, 10)
