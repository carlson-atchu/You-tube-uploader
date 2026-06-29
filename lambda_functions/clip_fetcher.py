"""
Clip Fetcher Lambda
Runs every 12 hours. Downloads 2 fresh HD thunderstorm/rain video clips
from Pexels and uploads them to S3. Audio is extracted from each clip
via ffmpeg so every video has a matching unique thunderstorm audio track.
"""

import http.client
import json
import logging
import os
import random
import subprocess
import urllib.parse
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
sm = boto3.client("secretsmanager")

VIDEO_SEARCH_TERMS = [
    "thunderstorm lightning",
    "heavy rain thunderstorm",
    "tropical thunderstorm",
    "lightning storm forest",
    "jungle thunderstorm rain",
    "rainstorm lightning",
    "thunder rain forest",
    "lightning rain jungle",
    "tropical storm rain",
    "forest storm lightning",
    "rain lightning night",
    "storm clouds lightning",
]

NATURE_PREFIX  = "assets/nature/"
AUDIO_PREFIX   = "assets/audio/"
VIDEO_IDS_KEY  = "assets/nature/downloaded_ids.json"
AUDIO_IDS_KEY  = "assets/audio/downloaded_ids.json"
CLIPS_PER_RUN  = 2

FFMPEG = "/opt/bin/ffmpeg"


def lambda_handler(event, context):
    bucket  = os.environ["S3_BUCKET_NAME"]
    api_key = _get_api_key(os.environ["PEXELS_SECRET_NAME"])

    video_ids = _load_ids(bucket, VIDEO_IDS_KEY)
    audio_ids = _load_ids(bucket, AUDIO_IDS_KEY)
    logger.info("Library: %d video clips, %d audio tracks", len(video_ids), len(audio_ids))

    videos_fetched, audio_fetched = _fetch_videos(api_key, video_ids, audio_ids, bucket)

    _save_ids(bucket, VIDEO_IDS_KEY, video_ids)
    _save_ids(bucket, AUDIO_IDS_KEY, audio_ids)

    logger.info("Done: %d video(s), %d audio(s) fetched this run", videos_fetched, audio_fetched)
    return {
        "statusCode": 200,
        "body": json.dumps({"videos_fetched": videos_fetched, "audio_fetched": audio_fetched}),
    }


# ── Video + audio fetching ────────────────────────────────────────────────────

def _fetch_videos(api_key: str, video_ids: set, audio_ids: set, bucket: str):
    videos_fetched = 0
    audio_fetched  = 0
    attempts       = 0

    while videos_fetched < CLIPS_PER_RUN and attempts < 15:
        attempts += 1
        term = random.choice(VIDEO_SEARCH_TERMS)
        page = random.randint(1, 8)
        try:
            videos = _search_videos(api_key, term, page=page)
        except Exception as e:
            logger.warning("Video search failed ('%s' p%d): %s", term, page, e)
            continue

        new = [v for v in videos if str(v["id"]) not in video_ids]
        if not new:
            continue

        video    = random.choice(new)
        video_id = str(video["id"])
        url      = _pick_hd_video_file(video)
        if not url:
            logger.warning("No HD file for video %s", video_id)
            continue

        tmp_video = f"/tmp/clip_{random.randint(0, 999999)}.mp4"
        try:
            _download_to_file(url, tmp_video)
        except Exception as e:
            logger.error("Video download failed %s: %s", video_id, e)
            _rm(tmp_video)
            continue

        # Upload video clip
        s3_video_key = f"{NATURE_PREFIX}pexels_{video_id}.mp4"
        try:
            s3.upload_file(tmp_video, bucket, s3_video_key)
            video_ids.add(video_id)
            videos_fetched += 1
            logger.info("Saved video %s ('%s') → %s", video_id, term, s3_video_key)
        except Exception as e:
            logger.error("S3 video upload failed %s: %s", video_id, e)
            _rm(tmp_video)
            continue

        # Extract audio track from the same clip
        audio_id = f"{video_id}_audio"
        if audio_id not in audio_ids:
            tmp_audio = f"/tmp/audio_{video_id}.mp3"
            try:
                _extract_audio(tmp_video, tmp_audio)
                s3_audio_key = f"{AUDIO_PREFIX}pexels_{video_id}.mp3"
                s3.upload_file(tmp_audio, bucket, s3_audio_key)
                audio_ids.add(audio_id)
                audio_fetched += 1
                logger.info("Extracted audio from %s → %s", video_id, s3_audio_key)
            except Exception as e:
                logger.warning("Audio extraction failed for %s: %s", video_id, e)
            finally:
                _rm(tmp_audio)

        _rm(tmp_video)

    return videos_fetched, audio_fetched


def _extract_audio(video_path: str, audio_path: str):
    result = subprocess.run(
        [FFMPEG, "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-q:a", "4", audio_path],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extract failed: {result.stderr[-300:].decode(errors='replace')}")


# ── Pexels video search helpers ───────────────────────────────────────────────

def _search_videos(api_key: str, query: str, page: int = 1, per_page: int = 15) -> list:
    params = urllib.parse.urlencode({
        "query": query, "per_page": per_page,
        "page": page, "orientation": "landscape",
    })
    conn = http.client.HTTPSConnection("api.pexels.com", timeout=15)
    conn.request("GET", f"/videos/search?{params}", headers=_headers(api_key))
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise RuntimeError(f"Pexels videos {resp.status}: {body[:200].decode(errors='replace')}")
    return json.loads(body).get("videos", [])


def _pick_hd_video_file(video: dict) -> str | None:
    files = video.get("video_files", [])
    hd = [f for f in files if f.get("quality") == "hd" and "mp4" in f.get("file_type", "")]
    sd = [f for f in files if f.get("quality") == "sd" and "mp4" in f.get("file_type", "")]
    chosen = (hd or sd or [None])[0]
    return chosen["link"] if chosen else None


# ── Shared helpers ────────────────────────────────────────────────────────────

def _headers(api_key: str) -> dict:
    return {"Authorization": api_key, "User-Agent": "YTNatureUploader/1.0"}


def _download_to_file(url: str, dest: str):
    parsed = urllib.parse.urlparse(url)
    path   = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    conn   = http.client.HTTPSConnection(parsed.netloc, timeout=300)
    conn.request("GET", path, headers={"User-Agent": "YTNatureUploader/1.0"})
    resp = conn.getresponse()
    if resp.status not in (200, 206):
        raise RuntimeError(f"Download HTTP {resp.status}")
    with open(dest, "wb") as f:
        while chunk := resp.read(8 * 1024 * 1024):
            f.write(chunk)


def _rm(path: str):
    try:
        os.remove(path)
    except Exception:
        pass


def _get_api_key(secret_name: str) -> str:
    resp = sm.get_secret_value(SecretId=secret_name)
    return json.loads(resp["SecretString"])["api_key"]


def _load_ids(bucket: str, key: str) -> set:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return set(json.loads(obj["Body"].read()))
    except Exception:
        return set()


def _save_ids(bucket: str, key: str, ids: set):
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(list(ids)).encode(),
        ContentType="application/json",
    )
