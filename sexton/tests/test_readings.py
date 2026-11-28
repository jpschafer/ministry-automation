"""Readings files: lookup, description text, and the init helper."""

from __future__ import annotations

import dataclasses
import json
from datetime import date

from sexton.lectionary import all_days, day_name
from sexton.readings import block, import_rcl, init, parse_ics, readings_for
from tests.test_reconcile import World


def write(folder, letter, data):
    (folder / f"year-{letter}.json").write_text(json.dumps(data))


def test_filled_in_readings_are_listed_in_order_and_blanks_skipped(tmp_path):
    write(tmp_path, "a", {"Proper 7": {"Old Testament": "Jeremiah 20:7-13", "Psalm": " ",
                                       "New Testament": "Romans 6:1b-11"}})
    found = readings_for(str(tmp_path), "Proper 7", "Year A")
    assert found == [("Old Testament", "Jeremiah 20:7-13"), ("New Testament", "Romans 6:1b-11")]
    assert block(found) == "Readings\nOld Testament: Jeremiah 20:7-13\nNew Testament: Romans 6:1b-11"


def test_missing_day_file_or_broken_json_means_no_readings(tmp_path):
    assert readings_for(str(tmp_path), "Proper 7", "Year A") == []  # no file
    write(tmp_path, "a", {"Proper 8": {"Old Testament": "x"}})
    assert readings_for(str(tmp_path), "Proper 7", "Year A") == []  # no entry
    (tmp_path / "year-b.json").write_text("{ not json")
    assert readings_for(str(tmp_path), "Proper 7", "Year B") == []
    assert block([]) == ""


def test_init_creates_every_day_and_never_overwrites(tmp_path):
    write(tmp_path, "a", {"Proper 7": {"Old Testament": "Genesis 21:8-21", "New Testament": ""},
                          "Homecoming": {"Old Testament": "Psalm 84"}})
    init(str(tmp_path))
    a = json.loads((tmp_path / "year-a.json").read_text())
    assert a["Proper 7"]["Old Testament"] == "Genesis 21:8-21"  # kept
    assert "Homecoming" in a  # extras kept
    assert a["First Sunday of Advent"] == {"Old Testament": "", "Gospel": ""}
    for letter in "bc":
        assert list(json.loads((tmp_path / f"year-{letter}.json").read_text())) == all_days()


def test_every_name_the_calendar_produces_has_a_slot():
    names = {day_name(date(2020, 1, 1).fromordinal(n)) for n in range(date(2020, 1, 1).toordinal(),
                                                                       date(2032, 1, 1).toordinal())}
    assert names <= set(all_days())


def test_stream_description_gets_the_days_readings(tmp_path):
    w = World()
    # FakeClock: Mon Jan 12, 1970 -> Baptism of the Lord, Year B
    write(tmp_path, "b", {"Baptism of the Lord": {"Old Testament": "Genesis 1:1-5", "New Testament": "Acts 19:1-7"}})
    w.config = dataclasses.replace(w.config, readings_dir=str(tmp_path))
    w.brain.config = w.config
    w.push("colfax")
    w.run()
    [vid] = w.graph.live("colfax-page")
    assert w.graph.videos[vid]["description"] == (
        "Readings\nOld Testament: Genesis 1:1-5\nNew Testament: Acts 19:1-7")


FEED = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Proper 7 (12) (Year B)\r\n"
    "DESCRIPTION:1 Samuel 17:(1a\\, 4-11\\, 19-23)\\, 32-49 and  Psalm 9:9-20 or 1 Sam"
    "uel 17:57-18:5\\, 18:10-1\r\n"
    " 6 and  Psalm 133\\nJob 38:1-11 and  Psalm 107:1-3\\, 23-32\\n2 Corinthians 6:1-13\\nMark 4:35-41\\nhttps://x\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:First Sunday of Advent (Year B)\r\n"
    "DESCRIPTION:Isaiah 64:1-9\\nPsalm 80:1-7\\, 17-19\\n1 Corinthians 1:3-9\\n"
    "Mark 13:24-37\\nhttps://x\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Third Sunday of Easter (Year B)\r\n"
    "DESCRIPTION:* Acts 3:12-19\\nPsalm 4\\n1 John 3:1-7\\nLuke 24:36b-48\\nhttps://x\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Reign of Christ - Proper 29 (34) (Year A)\r\n"
    "DESCRIPTION:Ezekiel 34:11-16\\, 20-24 and  Psalm 100\\nEzekiel 34:11-16\\, 20-24 and  Psalm 95:1-7a\\n"
    "Ephesians 1:15-23\\nMatthew 25:31-46\\nhttps://x\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Liturgy of the Palms (Year A)\r\n"
    "DESCRIPTION:Psalm 118:1-2\\, 19-29\\nMatthew 21:1-11\\nhttps://x\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Liturgy of the Passion (Year A)\r\n"
    "DESCRIPTION:Isaiah 50:4-9a\\nPsalm 31:9-16\\nPhilippians 2:5-11\\nMatthew 27:11-54\\nhttps://x\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nSUMMARY:Easter Vigil (Year A)\r\n"
    "DESCRIPTION:See the Easter Vigil page for full list of readings: https://x\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_rcl_feed_is_reduced_to_first_reading_and_gospel():
    days = parse_ics(FEED)
    assert days[("Year B", "Proper 7")] == {
        "Old Testament": "1 Samuel 17:(1a, 4-11, 19-23), 32-49 or 1 Samuel 17:57-18:5, 18:10-16 or Job 38:1-11",
        "Gospel": "Mark 4:35-41"}
    assert days[("Year B", "First Sunday of Advent")] == {"Old Testament": "Isaiah 64:1-9", "Gospel": "Mark 13:24-37"}
    assert days[("Year B", "Third Sunday of Easter")] == {"First Reading": "Acts 3:12-19", "Gospel": "Luke 24:36b-48"}
    assert days[("Year A", "Christ the King (Proper 29)")]["Old Testament"] == "Ezekiel 34:11-16, 20-24"
    assert days[("Year A", "Palm/Passion Sunday")] == {
        "Old Testament": "Isaiah 50:4-9a", "Gospel": "Matthew 21:1-11 (Palms) or Matthew 27:11-54 (Passion)"}
    assert not any("Vigil" in name for _, name in days)


def test_import_fills_blank_days_only(tmp_path):
    feed = tmp_path / "weekly.ics"
    feed.write_text(FEED)
    folder = tmp_path / "readings"
    folder.mkdir()
    write(folder, "b", {"Proper 7": {"Old Testament": "Job 38:1-11", "Gospel": ""}})
    import_rcl(str(folder), str(feed))
    b = json.loads((folder / "year-b.json").read_text())
    assert b["Proper 7"] == {"Old Testament": "Job 38:1-11", "Gospel": ""}  # yours: untouched
    assert b["First Sunday of Advent"]["Gospel"] == "Mark 13:24-37"
    assert b["Proper 8"] == {"Old Testament": "", "Gospel": ""}  # not in the feed: still blank
