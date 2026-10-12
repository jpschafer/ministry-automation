"""
video_sorter.py

Adds each of your videos to its church playlist (Levels UMC / Colfax UMC)
exactly once, then puts each playlist in chronological order, OLDEST FIRST.

Why oldest-first: YouTube appends a newly added video to the END of a playlist
by default, so an oldest-first list stays correct as new videos land. No
re-sorting needed after the first run.

Safe to re-run: videos already in a playlist are skipped, and only the items
that are actually out of order get moved.

Dry run by default. Nothing changes on YouTube until you pass --apply.

    python video_sorter.py            # show the plan
    python video_sorter.py --apply    # do it

Both playlists must be set to MANUAL sort in YouTube Studio
(Playlist > Sort > Manual), or YouTube rejects reordering.
"""

import argparse
import sys
from bisect import bisect_left
from pathlib import Path

from googleapiclient.errors import HttpError

# youtube_videos.py lives in the sibling ../api folder, so put that on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from youtube_videos import get_my_videos, get_service  # noqa: E402

# Playlist title on YouTube -> keywords (case-insensitive) that, when found in a
# VIDEO title, route that video to the playlist. Adjust to your naming scheme.
PLAYLISTS = {
    "Levels UMC": ["levels"],
    "Colfax UMC": ["colfax"],
}

WRITE_COST = 50  # quota units per playlistItems insert/update (daily default: 10,000)


# --------------------------------------------------------------------------- #
# YouTube lookups
# --------------------------------------------------------------------------- #

def find_playlist_ids(youtube, names):
    """Map each wanted playlist title to its playlist ID (case-insensitive)."""
    wanted = {n.lower(): n for n in names}
    found = {}
    page_token = None
    while True:
        resp = youtube.playlists().list(
            part="snippet", mine=True, maxResults=50, pageToken=page_token
        ).execute()
        for p in resp["items"]:
            canonical = wanted.get(p["snippet"]["title"].strip().lower())
            if canonical:
                found[canonical] = p["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    missing = [n for n in names if n not in found]
    if missing:
        sys.exit(f"Couldn't find these playlists on your channel: {', '.join(missing)}")
    return found


def get_playlist_items(youtube, playlist_id):
    """Current items of a playlist, in their current order."""
    items = []
    page_token = None
    while True:
        resp = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()
        for it in resp["items"]:
            items.append({
                "item_id": it["id"],
                "video_id": it["contentDetails"]["videoId"],
                "title": it["snippet"]["title"],
                "fallback_date": it["contentDetails"].get("videoPublishedAt"),
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


# --------------------------------------------------------------------------- #
# Classification and planning (pure logic, no API calls)
# --------------------------------------------------------------------------- #

def classify(videos):
    """Split videos into per-playlist buckets, plus unmatched and ambiguous."""
    buckets = {name: [] for name in PLAYLISTS}
    unmatched, ambiguous = [], []
    for v in videos:
        title = v["title"].lower()
        hits = [n for n, kws in PLAYLISTS.items() if any(k in title for k in kws)]
        if len(hits) == 1:
            buckets[hits[0]].append(v)
        elif hits:
            ambiguous.append(v)
        else:
            unmatched.append(v)
    return buckets, unmatched, ambiguous


def longest_increasing_subsequence(keys):
    """Indices of one longest strictly increasing subsequence of `keys`."""
    tail_keys, tail_idx = [], []
    prev = [-1] * len(keys)
    for i, k in enumerate(keys):
        pos = bisect_left(tail_keys, k)
        if pos == len(tail_keys):
            tail_keys.append(k)
            tail_idx.append(i)
        else:
            tail_keys[pos] = k
            tail_idx[pos] = i
        prev[i] = tail_idx[pos - 1] if pos > 0 else -1

    result = set()
    i = tail_idx[-1] if tail_idx else -1
    while i != -1:
        result.add(i)
        i = prev[i]
    return result


def plan_playlist(existing, new_videos):
    """
    existing:   items already in the playlist, in current order
                (dicts with item_id, video_id, title, key)
    new_videos: videos to add (dicts with video_id, title, key)

    Returns (moves, inserts):
      moves:   [(item, position)]  reorders needed among existing items
      inserts: [(video, position or None)]  None means plain append

    Items in the longest already-in-order run stay put; only the rest move,
    which keeps the number of (quota-costly) writes as low as possible.
    """
    desired = sorted(existing, key=lambda e: e["key"])
    keep_ids = {
        existing[i]["item_id"]
        for i in longest_increasing_subsequence([e["key"] for e in existing])
    }

    # Walking the desired order ascending, every earlier item is already
    # settled, so a moved item always lands right after desired[i - 1].
    working = list(existing)
    moves = []
    for i, e in enumerate(desired):
        if e["item_id"] in keep_ids:
            continue
        before = working.index(e)
        working.remove(e)
        if i == 0:
            pos = 0
        else:
            pred = desired[i - 1]
            pos = next(j for j, w in enumerate(working) if w["item_id"] == pred["item_id"]) + 1
        working.insert(pos, e)
        if pos != before:
            moves.append((e, pos))

    assert [w["item_id"] for w in working] == [d["item_id"] for d in desired]

    # New videos go in oldest-first, so each position is valid when it's used.
    inserts = []
    for v in sorted(new_videos, key=lambda x: x["key"]):
        pos = bisect_left([w["key"] for w in working], v["key"])
        inserts.append((v, None if pos == len(working) else pos))
        working.insert(pos, v)

    return moves, inserts


# --------------------------------------------------------------------------- #
# Applying the plan
# --------------------------------------------------------------------------- #

def apply_plan(youtube, playlist_id, moves, inserts):
    for item, pos in moves:
        youtube.playlistItems().update(
            part="snippet",
            body={
                "id": item["item_id"],
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": item["video_id"]},
                    "position": pos,
                },
            },
        ).execute()

    for video, pos in inserts:
        snippet = {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video["video_id"]},
        }
        if pos is not None:
            snippet["position"] = pos
        youtube.playlistItems().insert(part="snippet", body={"snippet": snippet}).execute()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # titles may contain non-ASCII

    parser = argparse.ArgumentParser(description="Sort videos into church playlists.")
    parser.add_argument("--apply", action="store_true", help="make the changes (default is a dry run)")
    args = parser.parse_args()

    youtube = get_service()
    videos = get_my_videos()
    dates = {v["video_id"]: v["published_at"] for v in videos}

    playlist_ids = find_playlist_ids(youtube, list(PLAYLISTS))
    buckets, unmatched, ambiguous = classify(videos)

    total_writes = 0
    plans = {}
    for name, playlist_id in playlist_ids.items():
        items = get_playlist_items(youtube, playlist_id)
        existing = [
            {
                **it,
                # Prefer the video's own publish date from your uploads list.
                "key": (dates.get(it["video_id"]) or it["fallback_date"] or "", it["video_id"], it["item_id"]),
            }
            for it in items
        ]
        present = {e["video_id"] for e in existing}
        new_videos = [
            {"video_id": v["video_id"], "title": v["title"], "key": (v["published_at"], v["video_id"], "")}
            for v in buckets[name]
            if v["video_id"] not in present
        ]

        moves, inserts = plan_playlist(existing, new_videos)
        plans[name] = (playlist_id, moves, inserts)
        total_writes += len(moves) + len(inserts)

        print(f"\n== {name} ==")
        print(f"{len(existing)} already in playlist, {len(inserts)} to add, {len(moves)} to reorder")
        for item, pos in moves:
            print(f"  move   -> #{pos + 1:<4} {item['title']}")
        for video, pos in inserts:
            where = "end" if pos is None else f"#{pos + 1}"
            print(f"  add    -> {where:<5} {video['title']}")

    if unmatched:
        print(f"\n{len(unmatched)} video(s) matched no playlist (left alone):")
        for v in unmatched:
            print(f"  {v['title']}")
    if ambiguous:
        print(f"\n{len(ambiguous)} video(s) matched BOTH playlists (left alone):")
        for v in ambiguous:
            print(f"  {v['title']}")

    print(f"\nPlanned writes: {total_writes} (~{total_writes * WRITE_COST} quota units of the 10,000 daily default)")

    if not args.apply:
        print("Dry run only. Re-run with --apply to make these changes.")
        return
    if total_writes == 0:
        print("Nothing to do.")
        return

    try:
        for name, (playlist_id, moves, inserts) in plans.items():
            apply_plan(youtube, playlist_id, moves, inserts)
            print(f"Done: {name}")
    except HttpError as err:
        text = str(err)
        if "quotaExceeded" in text:
            sys.exit("Hit the daily API quota. Safe to re-run tomorrow; it picks up where it left off.")
        if "manualSortRequired" in text:
            sys.exit("A playlist is set to automatic sorting. In YouTube Studio, set Sort > Manual, then re-run.")
        raise


if __name__ == "__main__":
    main()
