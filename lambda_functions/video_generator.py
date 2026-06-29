"""
Video Generation Lambda
Invoked by the upload handler when mode includes "generated".
Generates video content based on category:
  - kids_learning:      animated slideshow with TTS narration via MoviePy + pyttsx3
  - nature_relaxation:  loops ambient footage + overlays rain audio
"""

import json
import logging
import os
import boto3
import random
import subprocess
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

ASSETS_LOCAL = "/tmp/assets"


def _ensure_assets(bucket: str, prefix: str):
    """Download assets from S3 to /tmp/assets on first invocation; reused on warm starts."""
    keys = [
        ("nature/jungle_rain_clip.mp4", f"{ASSETS_LOCAL}/nature/jungle_rain_clip.mp4"),
        ("audio/rain_jungle_1.mp3",     f"{ASSETS_LOCAL}/audio/rain_jungle_1.mp3"),
        ("fonts/Nunito-Bold.ttf",       f"{ASSETS_LOCAL}/fonts/Nunito-Bold.ttf"),
    ]
    for rel, local_path in keys:
        if not os.path.exists(local_path):
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            s3_key = f"{prefix}{rel}"
            logger.info("Downloading s3://%s/%s → %s", bucket, s3_key, local_path)
            s3.download_file(bucket, s3_key, local_path)


def lambda_handler(event, context):
    category     = event.get("category", "kids_learning")
    topic        = event.get("topic", "")
    bucket       = os.environ["S3_BUCKET_NAME"]
    gen_prefix   = os.environ.get("GENERATED_PREFIX", "generated-videos/")
    assets_prefix = os.environ.get("ASSETS_PREFIX", "assets/")

    _ensure_assets(bucket, assets_prefix)

    logger.info("Generating %s video on topic: %s", category, topic)

    if category == "kids_learning":
        local_video = generate_kids_video(topic)
    else:
        local_video = generate_nature_video(topic)

    if not local_video or not os.path.exists(local_video):
        return {"statusCode": 500, "body": json.dumps({"error": "Video generation failed"})}

    # Upload generated video to S3
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_topic = topic.replace(" ", "_").replace("/", "-")[:40]
    s3_key = f"{gen_prefix}{category}_{safe_topic}_{timestamp}.mp4"

    logger.info("Uploading generated video to s3://%s/%s", bucket, s3_key)
    s3.upload_file(local_video, bucket, s3_key)
    os.remove(local_video)

    return {
        "statusCode": 200,
        "body": json.dumps({"s3_key": s3_key, "topic": topic})
    }


# ── Kids Learning Video ───────────────────────────────────────────────────────

def generate_kids_video(topic: str) -> str:
    """
    Creates a simple animated slideshow video with:
    - Colorful title card
    - 4–6 fact slides with large text
    - Background music (looped from /tmp/assets/bg_kids.mp3)
    - Text-to-speech narration per slide
    Uses: ffmpeg (Lambda layer) + PIL for image generation
    """
    from PIL import Image, ImageDraw, ImageFont
    import pyttsx3

    facts = _get_facts_for_topic(topic)
    slide_paths = []
    audio_paths = []

    font_path = "/tmp/assets/fonts/Nunito-Bold.ttf"  # bundled in Lambda layer
    bg_colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD"]

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, (heading, fact_text) in enumerate(facts):
            # — Image slide —
            img = Image.new("RGB", (1280, 720), color=bg_colors[i % len(bg_colors)])
            draw = ImageDraw.Draw(img)
            try:
                title_font = ImageFont.truetype(font_path, 72)
                body_font  = ImageFont.truetype(font_path, 40)
            except Exception:
                title_font = ImageFont.load_default()
                body_font  = title_font

            # Heading
            draw.text((640, 80), heading, font=title_font, fill="white", anchor="mm")
            # Body text (word-wrap)
            wrapped = textwrap.fill(fact_text, width=50)
            draw.multiline_text((640, 380), wrapped, font=body_font, fill="white",
                                align="center", anchor="mm", spacing=12)
            # Emoji decoration
            draw.text((640, 650), "⭐ Subscribe for more! ⭐", font=body_font, fill="white", anchor="mm")

            slide_path = os.path.join(tmpdir, f"slide_{i:02d}.png")
            img.save(slide_path)
            slide_paths.append(slide_path)

            # — TTS audio for this slide —
            tts_text = f"{heading}. {fact_text}"
            audio_path = os.path.join(tmpdir, f"audio_{i:02d}.wav")
            _tts_to_file(tts_text, audio_path)
            audio_paths.append(audio_path)

        # — Assemble with ffmpeg —
        output = tempfile.mktemp(suffix=".mp4")
        _assemble_slideshow(slide_paths, audio_paths, output)
        return output


def _get_facts_for_topic(topic: str) -> list[tuple[str, str]]:
    """Returns (heading, fact) pairs. In production, call Claude API for dynamic facts."""
    # Static fallback facts — extend or replace with Claude API call for dynamic content
    defaults = {
        "The Solar System and Planets": [
            ("Our Solar System 🌍", "Our solar system has 8 planets all orbiting around the Sun, a giant star!"),
            ("The Sun ☀️", "The Sun is so big that one million Earths could fit inside it!"),
            ("Planet Earth 🌎", "Earth is the only planet we know of that has living things on it."),
            ("Mars 🔴", "Mars is called the Red Planet because its soil contains lots of iron oxide — rust!"),
            ("Jupiter 🪐", "Jupiter is the biggest planet. Its famous Great Red Spot is a storm bigger than Earth!"),
            ("Fun Fact! 🚀", "It takes 8 minutes for sunlight to travel from the Sun to Earth."),
        ],
    }
    return defaults.get(topic, [
        ("Let's Learn! 📚", f"Today we're exploring: {topic}"),
        ("Did You Know? 🤔", "Science helps us understand the amazing world around us!"),
        ("Keep Exploring! 🌟", "Ask questions, stay curious, and never stop learning!"),
    ])


def _tts_to_file(text: str, output_path: str):
    """Generate WAV audio from text using espeak (available in Lambda layer)."""
    subprocess.run(
        ["espeak", "-w", output_path, "-s", "140", "-v", "en-us", text],
        check=True, capture_output=True
    )


def _assemble_slideshow(slide_paths: list, audio_paths: list, output: str):
    """Use ffmpeg to combine slides + audio into MP4."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Build input list file for ffmpeg
        concat_file = os.path.join(tmpdir, "concat.txt")
        combined_audio = os.path.join(tmpdir, "combined_audio.wav")

        # Combine audio files
        audio_inputs = []
        for ap in audio_paths:
            audio_inputs += ["-i", ap]
        subprocess.run(
            ["ffmpeg", "-y"] + audio_inputs +
            ["-filter_complex", f"concat=n={len(audio_paths)}:v=0:a=1[out]", "-map", "[out]", combined_audio],
            check=True, capture_output=True
        )

        # Get duration of each audio segment
        durations = [_get_audio_duration(ap) for ap in audio_paths]

        # Write concat file (image shown for duration of its audio)
        with open(concat_file, "w") as f:
            for slide, dur in zip(slide_paths, durations):
                f.write(f"file '{slide}'\nduration {dur:.2f}\n")
            f.write(f"file '{slide_paths[-1]}'\n")  # ffmpeg requires final entry

        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", combined_audio,
            "-c:v", "libx264", "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-shortest",
            output
        ], check=True, capture_output=True)


def _get_audio_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except Exception:
        return 5.0


# ── Nature Relaxation Video ───────────────────────────────────────────────────

def generate_nature_video(topic: str) -> str:
    """
    Assembles a 3-hour nature video by:
    1. Looping a base jungle/rain video from /tmp/assets/nature/
    2. Overlaying rain audio from /tmp/assets/audio/
    3. Adding subtle title overlay with ffmpeg
    Assets are downloaded from S3 to /tmp/assets on first invocation and reused on warm starts.
    """
    assets_dir  = "/tmp/assets/nature"
    audio_dir   = "/tmp/assets/audio"
    target_secs = 60 * 60 * 3   # 3 hours

    # Pick a base clip
    clips = list(Path(assets_dir).glob("*.mp4"))
    if not clips:
        logger.error("No nature clips found in %s", assets_dir)
        return None
    base_clip = str(random.choice(clips))

    # Pick rain audio
    rains = list(Path(audio_dir).glob("rain*.mp3"))
    rain_audio = str(random.choice(rains)) if rains else None

    output = tempfile.mktemp(suffix=".mp4")

    # Loop video to target duration
    loop_cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", base_clip,
        "-t", str(target_secs),
        "-c:v", "copy",
        "-an",
        "/tmp/looped_video.mp4"
    ]
    subprocess.run(loop_cmd, check=True, capture_output=True)

    if rain_audio:
        # Loop audio + mix
        audio_cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", rain_audio,
            "-t", str(target_secs),
            "-c:a", "aac", "-b:a", "192k",
            "/tmp/looped_audio.aac"
        ]
        subprocess.run(audio_cmd, check=True, capture_output=True)

        mix_cmd = [
            "ffmpeg", "-y",
            "-i", "/tmp/looped_video.mp4",
            "-i", "/tmp/looped_audio.aac",
            "-c:v", "copy", "-c:a", "copy",
            "-shortest",
            output
        ]
        subprocess.run(mix_cmd, check=True, capture_output=True)
    else:
        subprocess.run(["cp", "/tmp/looped_video.mp4", output], check=True)

    return output
