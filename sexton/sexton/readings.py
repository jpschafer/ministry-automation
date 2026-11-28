"""The readings listed in each stream's description, from files you fill in.

One JSON file per lectionary year in the readings folder (next to config.yaml):

  readings/year-a.json   {"Proper 7": {"Old Testament": "Jeremiah 20:7-13",
  readings/year-b.json                 "Gospel": "Matthew 10:24-39"}, ...}
  readings/year-c.json

Keys are the day names Sexton puts in titles; labels and their order are yours.
Blank entries are skipped. Files are re-read whenever a video is created, so an
edit takes effect without a restart. A missing or broken file never stops a
stream from going live: the description just goes without readings.

  python -m sexton.readings init <folder>   create/extend the three files with
                                            every day name (never overwrites)
  python -m sexton.readings show <folder> [YYYY-MM-DD]   what a date would get
  python -m sexton.readings import-rcl <folder> [feed]  prefill blank days from the
                                            Vanderbilt RCL calendar feed (never
                                            overwrites a day you've filled in)
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime

import requests

from .lectionary import all_days, describe
from .log import event

log = logging.getLogger("sexton.readings")
LABELS = ("Old Testament", "Gospel")
# Vanderbilt Divinity Library's weekly RCL feed: three years of Sundays and holy days.
RCL_FEED = "https://lectionary.library.vanderbilt.edu/wp-content/uploads/feeds/weekly.ics"
# Vanderbilt's names where they differ from Sexton's (sexton.lectionary).
RCL_NAMES = {
    "Nativity of the Lord - Proper I": "Christmas Eve",
    "Nativity of the Lord - Proper III": "Christmas Day",
    "Resurrection of the Lord": "Easter Sunday",
    "Reign of Christ - Proper 29 (34)": "Christ the King (Proper 29)",
}
# " and  Psalm 69:7-10, (11-15), 16-18", " and  Psalm 42 and 43": the psalm paired with a reading.
PAIRED_PSALM = re.compile(r"\s+and\s+Psalms?\s[^A-Za-z]*(?:[ab](?![a-z])[^A-Za-z]*)*(?:and\s+\d+[^A-Za-z]*)?")


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


def parse_ics(text):
    """{("Year A", day name): {label: reference}} from the RCL feed's text."""
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold continuation lines
    days, palms = {}, {}
    for vevent in text.split("BEGIN:VEVENT")[1:]:
        fields = {}
        for line in vevent.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields[key.split(";")[0]] = value
        match = re.fullmatch(r"(.+?) \((Year [ABC])\)", fields.get("SUMMARY", "").strip())
        if not match:
            continue
        name, year = match.groups()
        lines = [ln.strip() for ln in fields.get("DESCRIPTION", "").replace("\\,", ",").split("\\n")]
        lines = [ln for ln in lines if ln and not ln.startswith("http") and not ln.startswith("See the")]
        acts_first = bool(lines) and lines[0].startswith("*")
        lines = [ln.lstrip("* ").strip() for ln in lines]
        if name == "Liturgy of the Palms":
            palms[year] = lines[-1] if lines else ""
            continue
        if name == "Liturgy of the Passion":
            name = "Palm/Passion Sunday"
        name = RCL_NAMES.get(name) or re.sub(r"^(Proper \d+) \(\d+\)$", r"\1", name)
        entry = _readings_from(lines, acts_first)
        if entry:
            days[(year, name)] = entry
    for year, gospel in palms.items():  # Palm/Passion Sunday offers both Gospels
        entry = days.get((year, "Palm/Passion Sunday"))
        if entry and gospel:
            entry["Gospel"] = f"{gospel} (Palms) or {entry['Gospel']} (Passion)"
    return days


def _readings_from(lines, acts_first):
    """First reading (psalms dropped, both summer tracks joined by "or") and Gospel."""
    if len(lines) < 3:
        return None
    gospel, first_lines = lines[-1], lines[:-2]  # [..., letter, Gospel]
    firsts = []
    for line in first_lines:
        if line.startswith("Psalm") or line.startswith("Wisdom of Solomon 10"):
            continue  # a psalm (or canticle) line of its own
        reading = re.sub(r"\s+", " ", PAIRED_PSALM.sub(" ", line)).strip(" ,;")
        if reading and reading not in firsts:
            firsts.append(reading)
    if not firsts:
        return None
    label = "First Reading" if acts_first else "Old Testament"
    return {label: " or ".join(firsts), "Gospel": gospel}


def import_rcl(folder, source=RCL_FEED):
    """Fills every day that's still blank in the year files from the RCL feed."""
    if source.startswith("http"):
        resp = requests.get(source, timeout=30, headers={"User-Agent": "sexton-readings-import"})
        resp.raise_for_status()
        text = resp.text
    else:
        with open(source, encoding="utf-8") as f:
            text = f.read()
    found = parse_ics(text)
    init(folder)
    for letter in "abc":
        year = f"Year {letter.upper()}"
        path = os.path.join(folder, f"year-{letter}.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        filled = kept = 0
        for day, entry in data.items():
            new = found.get((year, day))
            if not new:
                continue
            if any(str(v or "").strip() for v in entry.values()):
                kept += 1  # you've written something here: leave it alone
                continue
            data[day] = new
            filled += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"{path}: {filled} day(s) filled from the RCL, {kept} already yours (untouched)")


def main(argv=None):
    """CLI: init or show."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) >= 2 and args[0] == "init":
        init(args[1])
        return 0
    if len(args) >= 2 and args[0] == "import-rcl":
        import_rcl(args[1], *args[2:3])
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
