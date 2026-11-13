# Sexton

The sexton rings the bells. This one goes live on Facebook whenever OBS/Mevo
starts pushing to a church's Restreamer channel, and ends the broadcast once the
push has been gone for 3 minutes. YouTube egress stays managed in the Restreamer UI.

## How it works

Every ~10 seconds, for each input (church) and each of its destinations (its Page,
plus the shared personal profile), Sexton reads:

- **pushing**: is the channel in Core's `GET /api/v3/rtmp`?
- **live**: does the target have an active live video titled for this input?
- **egress**: does Core have the process `sexton:<input>:<destination>`?

and converges:

| State | Action |
|---|---|
| pushing, not live, no egress | create a `LIVE_NOW` video, then an egress to its `secure_stream_url` |
| pushing, live, no egress | recreate the egress from the video's ingest URL (restart mid-service) |
| pushing, egress < 120s old, not live yet | wait (no duplicate video) |
| pushing, egress ≥ 120s old, not live | alert, delete egress so the next tick starts over |
| pushing, the profile is live for the *other* church, which has stopped | end that video, then start this one |
| pushing, the profile is live for the other church, which is *still pushing* | alert "contention", leave it alone |
| not pushing for > 180s | end this input's videos, delete its egresses |

No files, no database. Facebook and Core are the source of truth. The only things
held in memory are the "stopped pushing at" timer (a restart resets it, which can
only *delay* an end), a short Facebook read cache, and alert deduplication.

Egress processes are plain stream copy (`-c copy -f flv`) from the channel's
internal RTMP stream, the same source the Restreamer UI's own RTMP egresses use.
Sexton never creates, changes or deletes a process whose id doesn't start with `sexton:`.

## Alerts

Via ntfy, deduplicated (once per problem, at most hourly re-sends, plus a
"Recovered" note): Core unreachable, any create/end/egress failure, Facebook auth
or permission errors, not LIVE within 2 minutes, an unknown live video occupying a
target, profile contention, and unexpected exceptions. Healthchecks.io gets pinged
every loop (`/fail` when a tick had errors), so a stopped Sexton is noticed too.

## Configuration

`config.yaml` (see `config.example.yaml`) maps inputs to destinations. Secrets
come only from env vars:

| Variable | Purpose |
|---|---|
| `CORE_USERNAME`, `CORE_PASSWORD` | Restreamer Core API login (names set in config) |
| `FB_TOKEN_*` | Page tokens / user token with `publish_video` (names set in config) |
| `NTFY_URL` | Full topic URL, e.g. `https://ntfy.sh/your-topic` |
| `NTFY_TOKEN` | Optional ntfy access token |
| `HEALTHCHECK_URL` | Healthchecks.io ping URL |

The Graph API version is the single constant `GRAPH_API_VERSION` in `sexton/facebook.py`.

## Running

```sh
docker build -t sexton .
docker run --rm --env-file sexton.env -v $PWD:/config:ro sexton --dry-run --once
```

`--dry-run` performs all reads but logs (and skips) every create/end/delete,
ntfy alert and healthcheck ping. `--once` runs a single tick. Logs are JSON lines
on stdout. For Unraid, use `unraid-template.xml` (copy it to
`/boot/config/plugins/dockerMan/templates-user/my-sexton.xml`, then Add Container →
Template: sexton) or `docker-compose.example.yml`.

Tests: `pip install -r requirements-dev.txt && pytest`

## Renewing the Facebook user token

The user token (profile streaming) lasts about 60 days, and Facebook requires a
login for every new one. Page tokens don't expire. To renew:

```sh
docker exec -it sexton python -m sexton.renew_token
```

It prints the Graph API Explorer steps, asks for your app ID, app secret and the
short-lived token (hidden input; or set `FB_APP_ID` / `FB_APP_SECRET`), and prints
the new long-lived token, its expiry date, and fresh Page tokens. Paste them into
the container's variables and restart. It changes nothing on its own.

## To verify before the first real Sunday

These weren't confirmable from documentation. Each is isolated in one place in
the code:

1. **`broadcast_status` filter on `GET /{target}/live_videos`**: missing from
   the current reference docs. Sexton sends it *and* filters on `status`
   client-side, so a server that ignores it is harmless. Check in Graph Explorer
   that the call returns your live video at all (`facebook.py: active_live_videos`).
2. **Status of a `LIVE_NOW` video before frames arrive.** If it isn't `LIVE`,
   Sexton still won't duplicate it (the young egress counts as "pending"), but
   you may want to add that status to `ACTIVE_STATUSES` once observed.
3. **Graph API version**: the changelog lists v26.0 as the newest release, but
   the docs' examples already use v27.0. Bump the constant when v27.0 is listed.
4. **Channel name format from `/api/v3/rtmp`** on your instance: Sexton compares
   the last path segment (`/live/<id>.stream` → `<id>.stream`) with `rtmp_name`.
   Check with `curl` during a test push.
5. **How long Facebook waits before auto-ending a silent broadcast.** If it's
   under 3 minutes, a long drop ends the video on Facebook's side; Sexton alerts
   and starts a new video when the push returns.

Suggested dry run: start a test push on a weekday, run with `--dry-run` and
read the `create_live_video` / `create_egress` log lines. Then do a real run
with `privacy: {"value": "SELF"}` on the profile.
