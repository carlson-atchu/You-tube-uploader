"""
VideoSelector — Dual-stack video picker
  - "premade"   : picks the oldest un-uploaded video from S3 premade prefix
  - "generated" : triggers AI video generation and returns the result
  - "dual"      : alternates between premade and generated each cycle
"""

import json
import logging
import os
import random
import boto3
from datetime import datetime

logger = logging.getLogger(__name__)


class VideoSelector:
    def __init__(self, s3_client, bucket: str, premade_prefix: str, generated_prefix: str):
        self.s3               = s3_client
        self.bucket           = bucket
        self.premade_prefix   = premade_prefix
        self.generated_prefix = generated_prefix
        self._cycle_key       = "state/upload_cycle.json"

    # ── Public ────────────────────────────────────────────────────────────

    def pick_next_video(self, mode: str, category: str) -> dict | None:
        if mode == "premade":
            return self._pick_premade()
        if mode == "generated":
            return self._generate_video(category)
        # dual: alternate
        cycle = self._read_cycle()
        if cycle["next"] == "premade":
            video = self._pick_premade()
            if not video:                       # fallback to generated
                video = self._generate_video(category)
            self._write_cycle("generated")
        else:
            video = self._generate_video(category)
            if not video:
                video = self._pick_premade()
            self._write_cycle("premade")
        return video

    def mark_as_uploaded(self, key: str, youtube_id: str):
        """Move the uploaded file to the 'uploaded/' archive prefix."""
        new_key = key.replace(
            self.premade_prefix,   "uploaded/premade/"
        ).replace(
            self.generated_prefix, "uploaded/generated/"
        )
        self.s3.copy_object(
            Bucket=self.bucket,
            CopySource={"Bucket": self.bucket, "Key": key},
            Key=new_key,
            Metadata={"youtube_id": youtube_id, "uploaded_at": datetime.utcnow().isoformat()},
            MetadataDirective="REPLACE"
        )
        self.s3.delete_object(Bucket=self.bucket, Key=key)
        logger.info("Archived %s → %s", key, new_key)

    # ── Private ───────────────────────────────────────────────────────────

    def _pick_premade(self) -> dict | None:
        resp = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=self.premade_prefix)
        objects = [
            o for o in resp.get("Contents", [])
            if o["Key"].lower().endswith((".mp4", ".mov", ".avi", ".mkv"))
        ]
        if not objects:
            logger.warning("No pre-made videos found in s3://%s/%s", self.bucket, self.premade_prefix)
            return None
        # pick the oldest (FIFO queue)
        oldest = sorted(objects, key=lambda x: x["LastModified"])[0]
        # Try to read sidecar metadata JSON if present
        meta_key = oldest["Key"].rsplit(".", 1)[0] + ".json"
        topic = ""
        try:
            meta_obj = self.s3.get_object(Bucket=self.bucket, Key=meta_key)
            meta = json.loads(meta_obj["Body"].read())
            topic = meta.get("topic", "")
        except Exception:
            pass
        return {"key": oldest["Key"], "source": "premade", "topic": topic}

    def _generate_video(self, category: str) -> dict | None:
        """Invoke the video generation Lambda asynchronously and wait for the output."""
        lam = boto3.client("lambda")
        topics = _TOPIC_POOLS.get(category, ["general knowledge"])
        topic  = random.choice(topics)

        payload = json.dumps({"category": category, "topic": topic})
        logger.info("Invoking video generation Lambda for topic: %s", topic)

        resp = lam.invoke(
            FunctionName=os.environ["VIDEO_GEN_LAMBDA_NAME"],
            InvocationType="RequestResponse",   # sync – waits for generation
            Payload=payload.encode()
        )
        result = json.loads(resp["Payload"].read())
        if result.get("statusCode") != 200:
            logger.error("Video generation failed: %s", result)
            return None

        body = json.loads(result["body"])
        return {
            "key":    body["s3_key"],
            "source": "generated",
            "topic":  topic,
        }

    # ── Cycle state helpers ───────────────────────────────────────────────

    def _read_cycle(self) -> dict:
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=self._cycle_key)
            return json.loads(obj["Body"].read())
        except Exception:
            return {"next": "premade"}

    def _write_cycle(self, next_type: str):
        self.s3.put_object(
            Bucket=self.bucket,
            Key=self._cycle_key,
            Body=json.dumps({"next": next_type}).encode(),
            ContentType="application/json"
        )


# ── Topic pools per channel category ─────────────────────────────────────────

_TOPIC_POOLS = {
    "kids_learning": [
        "The Solar System and Planets",
        "How Do Animals Hibernate?",
        "Why Is the Sky Blue?",
        "How Do Volcanoes Erupt?",
        "The Water Cycle Explained",
        "What Are Dinosaurs?",
        "How Do Bees Make Honey?",
        "Counting and Numbers for Kids",
        "The Alphabet Song and Letter Sounds",
        "Basic Addition and Subtraction",
        "How Do Plants Grow?",
        "What Are the Five Senses?",
        "Animals of the Rainforest",
        "How Do Fish Breathe Underwater?",
        "Why Do Leaves Change Color?",
    ],
    "nature_relaxation": [
        "Jungle Rainfall at Dawn",
        "Amazon Rainforest Thunderstorm",
        "Tropical Waterfall with Birdsong",
        "Dense Forest Rain on Leaves",
        "Rainforest River at Night",
        "Misty Mountain Jungle Rain",
        "Coastal Jungle Rain with Ocean Sounds",
        "Bamboo Forest Rainfall",
        "Jungle Storm with Thunder",
        "Serene Rainforest Morning Rain",
    ],
}
