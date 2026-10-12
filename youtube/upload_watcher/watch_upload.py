"""
Watches a directory for new video files and uploads them to YouTube,
but only if the filename starts with an approved prefix (so it doubles
as a "has this been titled correctly" gate).

Runs directly on your desktop as a Task Scheduler job (logon trigger, long-running).

Setup:
  pip install watchdog google-api-python-client google-auth-oauthlib

First run will pop your browser for a one-time YouTube login. After that, the
refresh token is reused silently -- no more browser prompts unless you revoke access.

Env vars (override defaults below, e.g. via Task Scheduler's "Add arguments"
or by setting them in the system environment):
  WATCH_DIR            directory to watch (default C:\ChurchVideos\watch)
  YT_TOKEN_FILE         path to token.pickle
  YT_CLIENT_SECRETS     path to client_secret.json
"""

import os
import time
import pickle
import logging
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR = Path(os.environ.get("CHURCH_VIDEO_BASE", r"C:\Users\Jason Schafer\Staging\ChurchVideos"))
WATCH_DIR = Path(os.environ.get("WATCH_DIR", str(BASE_DIR / "watch")))
UPLOADED_DIR = WATCH_DIR / "uploaded"
TOKEN_FILE = os.environ.get("YT_TOKEN_FILE", str(BASE_DIR / "config" / "token.pickle"))
CLIENT_SECRETS_FILE = os.environ.get("YT_CLIENT_SECRETS", str(BASE_DIR / "config" / "client_secret.json"))
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

ALLOWED_PREFIXES = ("Colfax UMC", "Levels UMC")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}
STABLE_CHECK_SECONDS = 5
STABLE_CHECKS_REQUIRED = 2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("filewatch-upload")


def get_youtube_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing expired token...")
            creds.refresh(Request())
        else:
            # First run (or a revoked/unrecoverable token): pop the browser for a one-time login.
            # This only happens interactively -- fine for a desktop scheduled task running
            # in a logged-in session, not fine for a headless/service context.
            log.info("No valid token found, opening browser for one-time YouTube login...")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("youtube", "v3", credentials=creds)


def wait_until_stable(path: Path, checks=STABLE_CHECKS_REQUIRED, interval=STABLE_CHECK_SECONDS):
    """Waits until file size stops changing, so we don't upload a half-copied file."""
    last_size = -1
    stable_count = 0
    while stable_count < checks:
        if not path.exists():
            return False
        size = path.stat().st_size
        stable_count = stable_count + 1 if size == last_size else 0
        last_size = size
        time.sleep(interval)
    return True


def upload_video(youtube, path: Path, title: str):
    body = {
        "snippet": {"title": title, "description": "", "categoryId": "22"},
        "status": {"privacyStatus": "unlisted"},  # change to "private" or "public" as needed
    }
    media = MediaFileUpload(str(path), chunksize=-1, resumable=True, mimetype="video/*")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info(f"  {path.name}: {int(status.progress() * 100)}% uploaded")
    log.info(f"Uploaded '{path.name}' -> https://youtu.be/{response['id']}")
    return response["id"]


class Handler(FileSystemEventHandler):
    def __init__(self, youtube):
        self.youtube = youtube

    def on_created(self, event):
        if not event.is_directory:
            self.process(Path(event.src_path))

    def on_moved(self, event):
        if not event.is_directory:
            self.process(Path(event.dest_path))

    def process(self, path: Path):
        if not path.exists() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            return

        if not path.name.startswith(ALLOWED_PREFIXES):
            log.warning(
                f"Skipping '{path.name}': doesn't start with an approved prefix "
                f"({' / '.join(ALLOWED_PREFIXES)}). Rename it and it'll be picked up "
                f"on the next file event or container restart."
            )
            return

        log.info(f"Detected '{path.name}', waiting for the write to finish...")
        if not wait_until_stable(path):
            log.warning(f"'{path.name}' disappeared before it stabilized, skipping.")
            return

        title = path.stem  # filename minus extension becomes the video title
        try:
            upload_video(self.youtube, path, title)
            UPLOADED_DIR.mkdir(exist_ok=True)
            path.rename(UPLOADED_DIR / path.name)
            log.info(f"Moved '{path.name}' into uploaded/")
        except Exception as e:
            log.error(f"Upload failed for '{path.name}': {e}")


def scan_existing(handler: Handler):
    """Catches files that were already sitting in the directory before the watcher started."""
    for path in WATCH_DIR.iterdir():
        if path.is_file():
            handler.process(path)


def main():
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    youtube = get_youtube_service()
    handler = Handler(youtube)
    scan_existing(handler)

    observer = Observer()
    observer.schedule(handler, str(WATCH_DIR), recursive=False)
    observer.start()
    log.info(f"Watching {WATCH_DIR} for '{' / '.join(ALLOWED_PREFIXES)}' videos...")
    try:
        while True:
            time.sleep(30)  # just keeps the process alive; watchdog itself is event-driven, not polling
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()