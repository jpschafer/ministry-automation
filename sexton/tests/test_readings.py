"""Readings files: lookup, description text, and the init helper."""

from __future__ import annotations

import dataclasses
import json
from datetime import date

from sexton.lectionary import all_days, day_name
from sexton.readings import block, init, readings_for
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
    assert a["First Sunday of Advent"] == {"Old Testament": "", "New Testament": ""}
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
