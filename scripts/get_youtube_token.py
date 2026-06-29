#!/usr/bin/env python3
"""
get_youtube_token.py
────────────────────
Run this ONCE on your local machine to obtain a YouTube OAuth refresh token.
The refresh token never expires (unless revoked) and lets Lambda upload
videos without requiring browser interaction.

Prerequisites:
  pip install google-auth-oauthlib google-auth-httplib2

Steps:
  1. Go to https://console.cloud.google.com/
  2. Create a project → Enable "YouTube Data API v3"
  3. OAuth consent screen → External → Add your Google account as a test user
  4. Credentials → Create OAuth 2.0 Client ID → Desktop App
  5. Download client_secret_*.json → rename to client_secret.json
  6. Run:  python scripts/get_youtube_token.py
  7. Copy the printed JSON into AWS Secrets Manager secret named:
       <project_name>/youtube-credentials
"""

import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        "client_secret.json",   # downloaded from Google Cloud Console
        scopes=SCOPES
    )
    creds = flow.run_local_server(port=0)

    result = {
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    }

    print("\n✅  Copy the JSON below into AWS Secrets Manager:\n")
    print(json.dumps(result, indent=2))

    # Also save locally for reference
    with open("youtube_credentials.json", "w") as f:
        json.dump(result, f, indent=2)
    print("\n📁  Also saved to youtube_credentials.json (don't commit this!)")

if __name__ == "__main__":
    main()
