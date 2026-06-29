"""
Video Generation Lambda
Invoked by the upload handler when mode includes "generated".
Generates video content based on category:
  - kids_learning:      animated slideshow with TTS narration via MoviePy + pyttsx3
  - nature_relaxation:  loops ambient footage + overlays rain audio
"""

import json
import logging
import math
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

s3     = boto3.client("s3")
polly  = boto3.client("polly")

ASSETS_LOCAL = "/tmp/assets"


def _ensure_assets(bucket: str, prefix: str):
    """Download all assets from S3 to /tmp/assets, picking up new clips added by clip_fetcher."""
    SKIP = {"downloaded_ids.json"}
    for s3_subdir, local_dir in [
        (f"{prefix}nature/", f"{ASSETS_LOCAL}/nature"),
        (f"{prefix}audio/",  f"{ASSETS_LOCAL}/audio"),
        (f"{prefix}fonts/",  f"{ASSETS_LOCAL}/fonts"),
    ]:
        os.makedirs(local_dir, exist_ok=True)
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=s3_subdir)
        for obj in resp.get("Contents", []):
            filename = obj["Key"].rsplit("/", 1)[-1]
            if not filename or filename in SKIP:
                continue
            local_path = os.path.join(local_dir, filename)
            if not os.path.exists(local_path):
                logger.info("Downloading s3://%s/%s → %s", bucket, obj["Key"], local_path)
                s3.download_file(bucket, obj["Key"], local_path)


def lambda_handler(event, context):
    category     = event.get("category", "kids_learning")
    topic        = event.get("topic", "")
    bucket       = os.environ["S3_BUCKET_NAME"]
    gen_prefix   = os.environ.get("GENERATED_PREFIX", "generated-videos/")
    assets_prefix = os.environ.get("ASSETS_PREFIX", "assets/")

    _ensure_assets(bucket, assets_prefix)

    logger.info("Generating %s video on topic: %s", category, topic)

    duration_secs = event.get("duration_secs", 60 * 60 * 3)

    if category == "kids_learning":
        local_video = generate_kids_video(topic)
    else:
        local_video = generate_nature_video(topic, duration_secs=duration_secs)

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
    facts = _get_facts_for_topic(topic)
    slide_paths = []
    audio_paths = []
    font_path = "/tmp/assets/fonts/Nunito-Bold.ttf"

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, (heading, fact_text) in enumerate(facts):
            img = _make_cartoon_slide(i, heading, fact_text, font_path)
            slide_path = os.path.join(tmpdir, f"slide_{i:02d}.png")
            img.save(slide_path)
            slide_paths.append(slide_path)

            tts_text = f"{heading}. {fact_text}"
            audio_path = os.path.join(tmpdir, f"audio_{i:02d}.mp3")
            _tts_to_file(tts_text, audio_path)
            audio_paths.append(audio_path)

        output = tempfile.mktemp(suffix=".mp4")
        _assemble_slideshow(slide_paths, audio_paths, output)
        return output


def _make_cartoon_slide(idx: int, heading: str, fact_text: str, font_path: str):
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1280, 720
    PANEL_W = 400

    SCHEMES = [
        {"bg": "#1a1a2e", "panel": "#16213e", "char": "#FFD93D", "accent": "#FF6B6B"},
        {"bg": "#0f3460", "panel": "#1a1a5e", "char": "#FF9F43", "accent": "#00D2FF"},
        {"bg": "#1b4332", "panel": "#0d2818", "char": "#95D5B2", "accent": "#FFD700"},
        {"bg": "#4a0e8f", "panel": "#2d0057", "char": "#00CED1", "accent": "#FFD700"},
        {"bg": "#780000", "panel": "#4a0000", "char": "#2ECC71", "accent": "#FFE66D"},
        {"bg": "#023e8a", "panel": "#03045e", "char": "#F8961E", "accent": "#90E0EF"},
    ]
    EXPRESSIONS = ["happy", "excited", "thinking", "amazed", "happy", "excited"]
    scheme     = SCHEMES[idx % len(SCHEMES)]
    expression = EXPRESSIONS[idx % len(EXPRESSIONS)]

    img  = Image.new("RGB", (W, H), color=scheme["bg"])
    draw = ImageDraw.Draw(img)

    # Scattered star decorations on background
    rng = random.Random(idx * 137 + 7)
    for _ in range(35):
        sx, sy = rng.randint(0, W), rng.randint(0, H)
        sr = rng.uniform(2, 6)
        pts = _star_points(sx, sy, sr, sr * 0.4)
        draw.polygon(pts, fill=scheme["accent"])

    # Left panel (character area)
    draw.rounded_rectangle([15, 15, PANEL_W - 15, H - 15], radius=28, fill=scheme["panel"])

    # Cartoon character
    _draw_cartoon_character(draw, cx=PANEL_W // 2, cy=H // 2 - 10,
                            expression=expression,
                            char_color=scheme["char"],
                            accent=scheme["accent"])

    # Right panel (text area)
    draw.rounded_rectangle([PANEL_W + 10, 15, W - 15, H - 15], radius=28, fill=scheme["panel"])

    try:
        title_font = ImageFont.truetype(font_path, 54)
        body_font  = ImageFont.truetype(font_path, 33)
        sub_font   = ImageFont.truetype(font_path, 24)
    except Exception:
        title_font = body_font = sub_font = ImageFont.load_default()

    TXT_CX = PANEL_W + (W - PANEL_W) // 2

    draw.text((TXT_CX, 75), heading, font=title_font, fill=scheme["accent"], anchor="mm")
    draw.rounded_rectangle([PANEL_W + 40, 108, W - 40, 114], radius=3, fill=scheme["accent"])

    wrapped = textwrap.fill(fact_text, width=32)
    draw.multiline_text((TXT_CX, H // 2 + 15), wrapped,
                        font=body_font, fill="white",
                        align="center", anchor="mm", spacing=14)

    draw.text((TXT_CX, H - 48), "⭐  Subscribe for more fun learning!  ⭐",
              font=sub_font, fill=scheme["accent"], anchor="mm")

    return img


def _draw_cartoon_character(draw, cx: int, cy: int, expression: str = "happy",
                             char_color: str = "#FFD93D", accent: str = "#FF6B6B"):
    OL = "#222222"
    R  = 90   # body radius

    # Body
    draw.ellipse([cx-R, cy-R, cx+R, cy+R], fill=char_color, outline=OL, width=4)

    # Blush
    draw.ellipse([cx-R+8,  cy+22, cx-R+42, cy+44], fill="#ffb3b3")
    draw.ellipse([cx+R-42, cy+22, cx+R-8,  cy+44], fill="#ffb3b3")

    # Eyes
    EY = cy - 20
    ER = 17   # eye-white radius
    PR = 9    # pupil radius
    for ex in (cx - 33, cx + 33):
        draw.ellipse([ex-ER, EY-ER, ex+ER, EY+ER], fill="white", outline=OL, width=2)

    px_off = 5 if expression == "thinking" else 0
    py_off = -5 if expression == "thinking" else 0
    for ex in (cx - 33, cx + 33):
        draw.ellipse([ex+px_off-PR, EY+py_off-PR, ex+px_off+PR, EY+py_off+PR], fill="#111")
        draw.ellipse([ex+px_off-PR+2, EY+py_off-PR+2,
                      ex+px_off-PR+7, EY+py_off-PR+7], fill="white")

    # Eyebrows
    brow_y = EY - ER - 10
    if expression == "excited":
        for ex in (cx - 33, cx + 33):
            draw.arc([ex-ER-2, brow_y-10, ex+ER+2, brow_y+6], start=210, end=330, fill=OL, width=4)
    elif expression == "thinking":
        draw.line([cx-33-ER, brow_y+2, cx-33+ER, brow_y+2], fill=OL, width=4)
        draw.arc([cx+33-ER-2, brow_y-10, cx+33+ER+2, brow_y+6], start=210, end=330, fill=OL, width=4)
    else:
        for ex in (cx - 33, cx + 33):
            draw.arc([ex-ER-2, brow_y-6, ex+ER+2, brow_y+8], start=210, end=330, fill=OL, width=4)

    # Mouth
    MY = cy + 42
    if expression == "happy":
        draw.arc([cx-30, MY-14, cx+30, MY+14], start=10, end=170, fill=OL, width=5)
    elif expression == "excited":
        draw.chord([cx-35, MY-20, cx+35, MY+12], start=10, end=170, fill="white", outline=OL)
        draw.arc([cx-35, MY-20, cx+35, MY+12], start=10, end=170, fill=OL, width=4)
    elif expression == "thinking":
        draw.arc([cx+4, MY-12, cx+38, MY+8], start=200, end=350, fill=OL, width=4)
    elif expression == "amazed":
        draw.ellipse([cx-15, MY-18, cx+15, MY+8], fill=OL)
        draw.ellipse([cx-9, MY-12, cx+9, MY+2], fill="#cc3333")

    # Arms
    draw.rounded_rectangle([cx-R-36, cy+18, cx-R+8,  cy+58], radius=14, fill=char_color, outline=OL, width=3)
    draw.rounded_rectangle([cx+R-8,  cy+18, cx+R+36, cy+58], radius=14, fill=char_color, outline=OL, width=3)

    # Legs
    LEG_TOP = cy + R - 18
    for lx in (cx - 42, cx + 12):
        draw.rounded_rectangle([lx, LEG_TOP, lx + 30, LEG_TOP + 62], radius=12, fill=char_color, outline=OL, width=3)

    # Feet
    FOOT_Y = LEG_TOP + 50
    draw.ellipse([cx - 56, FOOT_Y, cx - 56 + 46, FOOT_Y + 24], fill=char_color, outline=OL, width=3)
    draw.ellipse([cx +  8, FOOT_Y, cx +  8 + 46, FOOT_Y + 24], fill=char_color, outline=OL, width=3)


def _star_points(cx: float, cy: float, r_outer: float, r_inner: float, n: int = 5):
    pts = []
    for i in range(n * 2):
        angle = math.pi / n * i - math.pi / 2
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


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
    """Generate MP3 audio from text using AWS Polly."""
    resp = polly.synthesize_speech(
        Text=text,
        OutputFormat="mp3",
        VoiceId="Joanna",
        Engine="neural",
    )
    with open(output_path, "wb") as f:
        f.write(resp["AudioStream"].read())


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

def generate_nature_video(topic: str, duration_secs: int = 60 * 60 * 3) -> str:
    """
    Assembles a nature video by looping a jungle/rain clip + rain audio overlay.
    duration_secs defaults to 3 hours; pass a shorter value for previews.
    Assets are downloaded from S3 to /tmp/assets on first invocation and reused on warm starts.
    """
    assets_dir  = "/tmp/assets/nature"
    audio_dir   = "/tmp/assets/audio"
    target_secs = duration_secs

    # Pick a base clip
    clips = list(Path(assets_dir).glob("*.mp4"))
    if not clips:
        logger.error("No nature clips found in %s", assets_dir)
        return None
    base_clip = str(random.choice(clips))

    # Pick any available audio track (original + Pexels downloads)
    rains = list(Path(audio_dir).glob("*.mp3"))
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
