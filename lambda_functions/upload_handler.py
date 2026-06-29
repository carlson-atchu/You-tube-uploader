"""
YouTube Auto-Upload Lambda Handler
Triggered every 6 hours via EventBridge
Supports: AI-generated videos + pre-made S3 videos (dual stack)
"""

import json
import os
import boto3
import logging
import random
from datetime import datetime

from video_selector import VideoSelector
from metadata_generator import MetadataGenerator
from youtube_uploader import YouTubeUploader

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
ssm = boto3.client("ssm")
secrets = boto3.client("secretsmanager")


def get_secret(secret_name: str) -> dict:
    response = secrets.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])


def lambda_handler(event, context):
    logger.info("YouTube Auto-Uploader triggered at %s", datetime.utcnow().isoformat())

    # ── Config from environment variables ──────────────────────────────────
    bucket_name        = os.environ["S3_BUCKET_NAME"]
    premade_prefix     = os.environ.get("PREMADE_PREFIX", "premade-videos/")
    generated_prefix   = os.environ.get("GENERATED_PREFIX", "generated-videos/")
    upload_mode        = os.environ.get("UPLOAD_MODE", "dual")   # "premade" | "generated" | "dual"
    channel_category   = os.environ.get("CHANNEL_CATEGORY", "kids_learning")  # or "nature_relaxation"

    # ── Secrets ─────────────────────────────────────────────────────────────
    yt_secret     = get_secret(os.environ["YOUTUBE_SECRET_NAME"])
    claude_secret = get_secret(os.environ["CLAUDE_SECRET_NAME"])

    # ── Decide what kind of video to upload this cycle ──────────────────────
    selector   = VideoSelector(s3, bucket_name, premade_prefix, generated_prefix)
    video_info = selector.pick_next_video(upload_mode, channel_category)

    if not video_info:
        logger.warning("No video available for upload. Skipping cycle.")
        return {"statusCode": 200, "body": "No video available"}

    logger.info("Selected video: %s (source: %s)", video_info["key"], video_info["source"])

    # ── Generate AI metadata ────────────────────────────────────────────────
    meta_gen = MetadataGenerator(
        api_key=claude_secret["api_key"],
        category=channel_category
    )
    metadata = meta_gen.generate(
        video_key=video_info["key"],
        topic=video_info.get("topic", ""),
        source=video_info["source"]
    )
    logger.info("Generated metadata: title='%s'", metadata["title"])

    # ── Download video from S3 to /tmp ──────────────────────────────────────
    local_path = f"/tmp/{os.path.basename(video_info['key'])}"
    s3.download_file(bucket_name, video_info["key"], local_path)
    logger.info("Downloaded video to %s", local_path)

    # ── Upload to YouTube ───────────────────────────────────────────────────
    uploader = YouTubeUploader(credentials=yt_secret)
    video_id = uploader.upload(
        file_path=local_path,
        title=metadata["title"],
        description=metadata["description"],
        tags=metadata["tags"],
        category_id=metadata["yt_category_id"],
        privacy="public"
    )

    logger.info("Successfully uploaded! YouTube video ID: %s", video_id)

    # ── Mark video as uploaded in S3 (move to 'uploaded/' prefix) ──────────
    selector.mark_as_uploaded(video_info["key"], video_id)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message":   "Upload successful",
            "video_id":  video_id,
            "title":     metadata["title"],
            "source":    video_info["source"],
        })
    }
