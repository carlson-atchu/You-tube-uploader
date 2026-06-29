# 📺 YouTube Auto-Uploader — AWS Serverless

Automatically uploads videos to your YouTube channel every **6 hours** using AWS Lambda + EventBridge.

Supports a **dual-stack** approach:
- 🤖 **AI-generated videos** — kids learning slideshows with TTS narration, or looped jungle rain visuals
- 📁 **Pre-made videos** — drop your own MP4s into S3 and they'll be queued for upload
- 🧠 **AI-generated metadata** — Claude writes the title, description, and tags for every video

---

## Architecture

```
EventBridge (every 6h)
        │
        ▼
┌──────────────────┐       ┌──────────────────────┐
│  upload_handler  │──────▶│  video_generator     │
│  Lambda          │invoke │  Lambda              │
│                  │◀──────│  (when mode=generated│
└────────┬─────────┘       │   or dual)           │
         │                 └──────────────────────┘
         │ S3 get/put
         ▼
┌──────────────────┐
│   S3 Bucket      │
│  premade-videos/ │
│  generated-videos│
│  uploaded/       │
│  state/          │
└──────────────────┘
         │
         │ YouTube Data API v3
         ▼
    Your YouTube Channel 🎬
```

---

## Quick Start

### 1. Prerequisites

- AWS CLI configured (`aws configure`)
- Terraform ≥ 1.6 installed
- Docker (for building Lambda layers)
- Python 3.12 locally
- A Google Cloud project with YouTube Data API v3 enabled

### 2. Get YouTube OAuth Credentials

```bash
# Install dependencies
pip install google-auth-oauthlib

# Run the OAuth flow (opens browser once)
python scripts/get_youtube_token.py
```

Copy the printed JSON into AWS Secrets Manager under the name shown in the Terraform output.

### 3. Add Claude API Key

Store your Anthropic API key in Secrets Manager:

```bash
aws secretsmanager create-secret \
  --name "yt-auto-uploader/claude-api-key" \
  --secret-string '{"api_key": "sk-ant-YOUR_KEY_HERE"}'
```

### 4. Add Your Assets

Populate the `assets/` directory before building layers:

```
assets/
  fonts/
    Nunito-Bold.ttf        ← Download free from fonts.google.com
  audio/
    rain_jungle_1.mp3      ← Royalty-free from freesound.org or pixabay.com
    rain_thunder.mp3
  nature/
    jungle_rain_clip.mp4   ← Short 30–60s loop clip (royalty-free)
    waterfall_clip.mp4
```

### 5. Build Lambda Layers

```bash
chmod +x scripts/build_layers.sh
./scripts/build_layers.sh
```

### 6. Deploy Infrastructure

```bash
cd infrastructure
terraform init
terraform apply
```

Note the S3 bucket name from the output.

### 7. Upload Pre-Made Videos (Optional)

```bash
# Upload your own videos to the premade queue
aws s3 cp my_kids_video.mp4 s3://YOUR_BUCKET/premade-videos/

# Optional: add a metadata sidecar for custom topics
echo '{"topic": "The Water Cycle"}' > my_kids_video.json
aws s3 cp my_kids_video.json s3://YOUR_BUCKET/premade-videos/
```

---

## Configuration

Edit environment variables in `infrastructure/main.tf`:

| Variable           | Default          | Options                                      |
|--------------------|------------------|----------------------------------------------|
| `UPLOAD_MODE`      | `dual`           | `premade`, `generated`, `dual`               |
| `CHANNEL_CATEGORY` | `kids_learning`  | `kids_learning`, `nature_relaxation`         |
| `schedule_hours`   | `6`              | Any positive integer                         |

---

## How Dual-Stack Works

In `dual` mode, the system alternates every cycle:

```
Cycle 1 → Premade video (oldest in S3 queue)
Cycle 2 → AI-generated video (random topic from pool)
Cycle 3 → Premade video
...
```

If the selected source has no content (e.g., S3 queue is empty), it automatically falls back to the other source.

---

## Extending the Topic Pool

Edit `lambda_functions/video_selector.py` → `_TOPIC_POOLS` to add more video topics:

```python
"kids_learning": [
    "How Do Rainbows Form?",
    "What Are Black Holes?",
    # add more topics here
],
"nature_relaxation": [
    "Monsoon Rainforest at Midnight",
    # add more here
]
```

---

## Monitoring

- **CloudWatch Logs**: Lambda execution logs under `/aws/lambda/yt-auto-uploader-*`
- **CloudWatch Alarm**: Fires when upload handler fails 2+ times in a 6-hour window
- **Uploaded archive**: Successfully uploaded videos move to `s3://BUCKET/uploaded/` and expire after 90 days

---

## Cost Estimate (approximate)

| Service            | Monthly Cost     |
|--------------------|------------------|
| Lambda (2 fns × 4/day × 30d) | ~$0.50    |
| S3 (50GB videos)   | ~$1.15           |
| Secrets Manager    | ~$0.80           |
| EventBridge        | Free tier        |
| **Total**          | **~$2.50/month** |

---

## File Structure

```
youtube-auto-uploader/
├── lambda_functions/
│   ├── upload_handler.py      # Main Lambda — orchestrates upload cycle
│   ├── video_selector.py      # Dual-stack video picker
│   ├── metadata_generator.py  # Claude-powered title/description/tags
│   ├── youtube_uploader.py    # YouTube Data API v3 client
│   └── video_generator.py     # AI video generation (kids + nature)
├── infrastructure/
│   └── main.tf                # All AWS resources (Terraform)
├── scripts/
│   ├── get_youtube_token.py   # One-time OAuth flow
│   └── build_layers.sh        # Build Lambda layers with Docker
├── config/
│   └── requirements.txt
└── README.md
```
