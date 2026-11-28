"""Lectionary names and years against published RCL calendars."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from sexton.lectionary import day_name, describe, easter, year_letter


@pytest.mark.parametrize("year,expected", [(2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)),
                                           (2027, date(2027, 3, 28)), (2038, date(2038, 4, 25))])
def test_easter(year, expected):
    assert easter(year) == expected


@pytest.mark.parametrize(
    "when,name,year",
    [
        (date(2026, 11, 29), "First Sunday of Advent", "B"),
        (date(2026, 12, 20), "Fourth Sunday of Advent", "B"),
        (date(2026, 11, 22), "Christ the King (Proper 29)", "A"),
        (date(2025, 12, 28), "First Sunday after Christmas Day", "A"),
        (date(2026, 1, 4), "Second Sunday after Christmas Day", "A"),
        (date(2026, 1, 11), "Baptism of the Lord", "A"),
        (date(2026, 1, 18), "Second Sunday after the Epiphany", "A"),
        (date(2026, 2, 15), "Transfiguration Sunday", "A"),
        (date(2026, 2, 18), "Ash Wednesday", "A"),
        (date(2026, 2, 22), "First Sunday in Lent", "A"),
        (date(2026, 3, 22), "Fifth Sunday in Lent", "A"),
        (date(2026, 3, 29), "Palm/Passion Sunday", "A"),
        (date(2026, 4, 2), "Maundy Thursday", "A"),
        (date(2026, 4, 3), "Good Friday", "A"),
        (date(2026, 4, 5), "Easter Sunday", "A"),
        (date(2026, 4, 12), "Second Sunday of Easter", "A"),
        (date(2026, 5, 14), "Ascension of the Lord", "A"),
        (date(2026, 5, 17), "Seventh Sunday of Easter", "A"),
        (date(2026, 5, 24), "Day of Pentecost", "A"),
        (date(2026, 5, 31), "Trinity Sunday", "A"),
        (date(2026, 6, 7), "Proper 5", "A"),
        (date(2026, 6, 21), "Proper 7", "A"),
        (date(2025, 6, 22), "Proper 7", "C"),
        (date(2026, 11, 26), "Thanksgiving Day", "A"),
        (date(2026, 12, 25), "Christmas Day", "B"),
        (date(2027, 1, 6), "Epiphany of the Lord", "B"),
    ],
)
def test_named_days(when, name, year):
    assert describe(when) == (name, f"Year {year}")


def test_weekdays_take_the_nearest_sunday():
    assert day_name(date(2026, 6, 24)) == "Proper 7"   # Wednesday: the Sunday before
    assert day_name(date(2026, 11, 28)) == "First Sunday of Advent"  # Saturday: the Sunday after
    assert describe(date(2026, 11, 28))[1] == "Year B"  # year follows the named Sunday


def test_christmas_eve_on_a_sunday_depends_on_the_hour():
    assert day_name(datetime(2023, 12, 24, 10)) == "Fourth Sunday of Advent"
    assert day_name(datetime(2023, 12, 24, 19)) == "Christmas Eve"
    assert day_name(datetime(2026, 12, 24, 19)) == "Christmas Eve"  # a Thursday


def test_sunday_christmas_reached_from_a_weekday_is_not_advent():
    assert day_name(date(2022, 12, 23)) == "Christmas Day"  # Friday before a Sunday Christmas


def test_year_turns_over_at_advent():
    assert year_letter(date(2026, 11, 28)) == "A"
    assert year_letter(date(2026, 11, 29)) == "B"
    assert year_letter(date(2027, 11, 28)) == "C"
