"""Names a date in the Revised Common Lectionary (RCL), as used by the UMC.

  day_name(dt)  -> "Proper 7", "First Sunday of Advent", "Ash Wednesday", ...
  year_letter(d) -> "A" | "B" | "C"

A holy day that falls on a weekday (Christmas Eve, Ash Wednesday, Good Friday...)
is named for itself. Any other day is named for the nearest Sunday: Monday to
Wednesday look back, Thursday to Saturday look ahead, so a midweek service
takes the Sunday it belongs to. The lectionary year turns over on the First
Sunday of Advent; Advent 2025 began Year A.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

ORDINALS = ("First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth")
SUNDAY = 6  # date.weekday()
# A Sunday Christmas Eve service at or after this hour is "Christmas Eve", not Advent 4.
CHRISTMAS_EVE_HOUR = 15


def easter(year):
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def advent_sunday(year):
    """First Sunday of Advent: the fourth Sunday before Christmas Day."""
    christmas = date(year, 12, 25)
    advent4 = christmas - timedelta(days=(christmas.weekday() - SUNDAY) % 7 or 7)
    return advent4 - timedelta(weeks=3)


def year_letter(d):
    """The RCL year (A, B or C) that d falls in."""
    liturgical = d.year + 1 if d >= advent_sunday(d.year) else d.year
    return "ABC"[(liturgical - 2023) % 3]  # Advent 2022 began Year A (liturgical 2023)


def _thanksgiving(year):
    nov1 = date(year, 11, 1)
    return nov1 + timedelta(days=(3 - nov1.weekday()) % 7 + 21)  # fourth Thursday


def holy_day(dt):
    """The name of the holy day dt falls on, or None."""
    d = dt.date() if isinstance(dt, datetime) else dt
    e = easter(d.year)
    fixed = {
        (12, 25): "Christmas Day",
        (1, 6): "Epiphany of the Lord",
        (11, 1): "All Saints Day",
    }
    moveable = {
        e - timedelta(days=46): "Ash Wednesday",
        e - timedelta(days=3): "Maundy Thursday",
        e - timedelta(days=2): "Good Friday",
        e - timedelta(days=1): "Holy Saturday",
        e + timedelta(days=39): "Ascension of the Lord",
        _thanksgiving(d.year): "Thanksgiving Day",
    }
    if (d.month, d.day) == (12, 24):
        late = isinstance(dt, datetime) and dt.hour >= CHRISTMAS_EVE_HOUR
        if d.weekday() != SUNDAY or late:
            return "Christmas Eve"
        return None
    return fixed.get((d.month, d.day)) or moveable.get(d)


def nearest_sunday(d):
    """d if it's a Sunday; otherwise the previous Sunday (Mon-Wed) or the next (Thu-Sat)."""
    back = (d.weekday() - SUNDAY) % 7
    return d - timedelta(days=back) if back <= 3 else d + timedelta(days=7 - back)


def sunday_name(s):
    """The RCL name of Sunday s."""
    # One branch per season of the church year reads more clearly than a table.
    # pylint: disable=too-many-return-statements,too-many-branches
    e = easter(s.year)
    advent1 = advent_sunday(s.year)
    if s.month == 12 and s.day == 25:
        return "Christmas Day"
    if advent1 <= s < date(s.year, 12, 25):
        return f"{ORDINALS[(s - advent1).days // 7]} Sunday of Advent"
    if s.month == 12 and s.day >= 26:
        return "First Sunday after Christmas Day"
    if s.month == 1 and s.day <= 13:
        if s.day == 1:
            return "First Sunday after Christmas Day"
        if s.day <= 5:
            return "Second Sunday after Christmas Day"
        if s.day == 6:
            return "Epiphany of the Lord"
        return "Baptism of the Lord"
    if s == e - timedelta(days=49):
        return "Transfiguration Sunday"
    if s < e - timedelta(days=49):
        baptism = date(s.year, 1, 7) + timedelta(days=(SUNDAY - date(s.year, 1, 7).weekday()) % 7)
        return f"{ORDINALS[(s - baptism).days // 7]} Sunday after the Epiphany"
    if s < e - timedelta(days=7):
        return f"{ORDINALS[(s - (e - timedelta(days=42))).days // 7]} Sunday in Lent"
    if s == e - timedelta(days=7):
        return "Palm/Passion Sunday"
    if s == e:
        return "Easter Sunday"
    if s < e + timedelta(days=49):
        return f"{ORDINALS[(s - e).days // 7]} Sunday of Easter"
    if s == e + timedelta(days=49):
        return "Day of Pentecost"
    if s == e + timedelta(days=56):
        return "Trinity Sunday"
    proper = (s - date(s.year, 5, 8)).days // 7 + 1  # Proper 1 is the Sunday of May 8-14
    return "Christ the King (Proper 29)" if proper == 29 else f"Proper {proper}"


def day_name(dt):
    """The lectionary name for a service at dt (a date or datetime)."""
    return holy_day(dt) or sunday_name(nearest_sunday(dt.date() if isinstance(dt, datetime) else dt))


def describe(dt):
    """(day name, "Year X") for a service at dt."""
    d = dt.date() if isinstance(dt, datetime) else dt
    named = d if holy_day(dt) else nearest_sunday(d)
    return day_name(dt), f"Year {year_letter(named)}"
