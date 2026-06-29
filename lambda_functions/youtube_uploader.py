"""
YouTubeUploader — Handles OAuth2 + video upload to YouTube Data API v3.
Credentials are stored in AWS Secrets Manager as a JSON object containing:
  {
    "client_id": "...",
    "client_secret": "...",
    "refresh_token": "..."    ← obtained once via OAuth consent flow
  }
"""

import logging
import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
API_SERVICE_NAME     = "youtube"
API_VERSION          = "v3"


class YouTubeUploader:
    def __init__(self, credentials: dict):
        """
        credentials: dict with keys client_id, client_secret, refresh_token
        """
        self.creds_dict = credentials
        self._youtube   = None

    @property
    def youtube(self):
        if self._youtube is None:
            self._youtube = self._build_service()
        return self._youtube

    def upload(
        self,
        file_path:   str,
        title:       str,
        description: str,
        tags:        list[str],
        category_id: str  = "27",
        privacy:     str  = "public",
    ) -> str:
        """Upload a video and return the YouTube video ID."""
        body = {
            "snippet": {
                "title":       title[:100],         # YouTube hard limit
                "description": description[:5000],  # YouTube hard limit
                "tags":        tags[:500],
                "categoryId":  category_id,
            },
            "status": {
                "privacyStatus":           privacy,
                "selfDeclaredMadeForKids": category_id == "27",  # flag kids content
            }
        }

        media = MediaFileUpload(
            file_path,
            mimetype="video/*",
            resumable=True,
            chunksize=5 * 1024 * 1024  # 5 MB chunks
        )

        logger.info("Starting YouTube upload: '%s'", title)
        request  = self.youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("Upload progress: %d%%", int(status.progress() * 100))

        video_id = response["id"]
        logger.info("Upload complete. Video ID: %s", video_id)
        return video_id

    # ── Private ───────────────────────────────────────────────────────────

    def _build_service(self):
        creds = Credentials(
            token         = None,
            refresh_token = self.creds_dict["refresh_token"],
            client_id     = self.creds_dict["client_id"],
            client_secret = self.creds_dict["client_secret"],
            token_uri     = "https://oauth2.googleapis.com/token",
            scopes        = [YOUTUBE_UPLOAD_SCOPE],
        )
        # Refresh access token
        creds.refresh(Request())
        return build(API_SERVICE_NAME, API_VERSION, credentials=creds)
