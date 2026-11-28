"""The readings listed in each stream's description, from files you fill in.

One JSON file per lectionary year in the readings folder (next to config.yaml):

  readings/year-a.json   {"Proper 7": {"Old Testament": "Jeremiah 20:7-13",
  readings/year-b.json                 "New Testament": "Romans 6:1b-11"}, ...}
  readings/year-c.json

Keys are the day names Sexton puts in titles; labels and their order are yours.
Blank entries are skipped. Files are re-read whenever a video is created, so an
edit takes effect without a restart. A missing or broken file never stops a
stream from going live: the description just goes without readings.

  python -m sexton.readings init <folder>   create/extend the three files with
                                            every day name (never overwrites)
  python -m sexton.readings show <folder> [YYYY-MM-DD]   what a date would get
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime

from .lectionary import all_days, describe
from .log import event

log = logging.getLogger("sexton.readings")
LABELS = ("Old Testament", "New Testament")


def path_for(folder, year):
    """The file for "Year A" etc."""
    return os.path.join(folder, f"year-{year.split()[-1].lower()}.json")


def readings_for(folder, day, year):
    """[(label, reference), ...] filled in for day, or [] if none."""
    if not folder:
        return []
    path = path_for(folder, year)
    try:
        with open(path, encoding="utf-8") as f:
            entry = (json.load(f) or {}).get(day) or {}
    except FileNotFoundError:
        return []
    except (OSError, ValueError, AttributeError) as err:
        event(log, logging.WARNING, "readings_unreadable", path=path, error=str(err))
        return []
    if not isinstance(entry, dict):
        return []
    return [(label, str(ref).strip()) for label, ref in entry.items() if str(ref or "").strip()]


def block(readings):
    """The description text for a list of readings."""
    if not readings:
        return ""
    return "Readings\n" + "\n".join(f"{label}: {ref}" for label, ref in readings)


def init(folder):
    """Creates the three files, or adds any missing day names to existing ones."""
    os.makedirs(folder, exist_ok=True)
    for letter in "abc":
        path = os.path.join(folder, f"year-{letter}.json")
        data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        added = 0
        ordered = {}
        for day in all_days():
            if day not in data:
                added += 1
            ordered[day] = data.get(day, {label: "" for label in LABELS})
        ordered.update({k: v for k, v in data.items() if k not in ordered})  # keep extras
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ordered, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"{path}: {added} day(s) added, {len(ordered)} total")


def main(argv=None):
    """CLI: init or show."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) >= 2 and args[0] == "init":
        init(args[1])
        return 0
    if len(args) >= 2 and args[0] == "show":
        when = datetime.fromisoformat(args[2]) if len(args) > 2 else datetime.now()
        day, year = describe(when)
        print(f"{when:%a %b %-d, %Y}: {day}, {year}  ({path_for(args[1], year)})")
        print(block(readings_for(args[1], day, year)) or "(no readings filled in)")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
