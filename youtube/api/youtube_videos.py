"""
youtube_videos.py

Import and call get_my_videos() to list every video on your channel.

    from youtube_videos import get_my_videos
    videos = get_my_videos()

Setup:
    pip install google-api-python-client google-auth-oauthlib google-auth-httplib2

    1. In Google Cloud Console, enable "YouTube Data API v3".
    2. Create OAuth credentials of type "Desktop app" and download the JSON.
    3. Save it as client_secret.json next to this file.
    4. First run opens a browser for consent; the token is cached in token.json.
"""

import json
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Full "youtube" scope (not readonly) so video_sorter.py can edit playlists.
SCOPES = ["https://www.googleapis.com/auth/youtube"]

# Anchored to this file so imports work no matter where the caller runs from.
_BASE_DIR = Path(__file__).resolve().parent
CLIENT_SECRET_FILE = _BASE_DIR / "client_secret.json"
TOKEN_FILE = _BASE_DIR / "token.json"
DEFAULT_OUTPUT_FILE = _BASE_DIR / "my_videos.json"


def _get_credentials() -> Credentials:
    creds = None
    if TOKEN_FILE.exists():
        saved = json.loads(TOKEN_FILE.read_text())
        # A token cached under narrower scopes (e.g. the old read-only one)
        # can't be reused, so only accept it if it covers everything we need.
        if set(SCOPES) <= set(saved.get("scopes") or []):
            creds = Credentials.from_authorized_user_info(saved, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = None  # token revoked or expired; fall through to a fresh login

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
        creds = flow.run_local_server(port=0)

    TOKEN_FILE.write_text(creds.to_json())
    return creds


def get_service():
    """Authenticated YouTube Data API client, shared with other scripts."""
    return build("youtube", "v3", credentials=_get_credentials())


def get_my_videos(output_file: str | Path | None = DEFAULT_OUTPUT_FILE) -> list[dict]:
    """
    Return every video on the authenticated user's channel, newest first,
    and write the list to a JSON file.

    output_file: where to write the JSON (default: my_videos.json next to this file).
                 Pass None to skip writing and just get the list back.

    Each item: {"video_id", "title", "published_at", "privacy_status", "url"}
    Private and unlisted videos are included because you're authenticated as the owner.
    """
    youtube = get_service()

    # Every channel has a hidden "uploads" playlist containing all its videos.
    channel = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads_id = channel["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    videos = []
    page_token = None
    while True:
        response = youtube.playlistItems().list(
            part="snippet,contentDetails,status",
            playlistId=uploads_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        for item in response["items"]:
            video_id = item["contentDetails"]["videoId"]
            videos.append({
                "video_id": video_id,
                "title": item["snippet"]["title"],
                # Private videos omit videoPublishedAt; the snippet time is a safe fallback.
                "published_at": (
                    item["contentDetails"].get("videoPublishedAt")
                    or item["snippet"]["publishedAt"]
                ),
                "privacy_status": item["status"]["privacyStatus"],
                "url": f"https://www.youtube.com/watch?v={video_id}",
            })

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    if output_file is not None:
        Path(output_file).write_text(
            json.dumps(videos, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return videos


if __name__ == "__main__":
    for v in get_my_videos():
        print(f'{v["published_at"]}  {v["privacy_status"]:<9}  {v["title"]}  ({v["url"]})')
