#!/bin/bash
# build_layers.sh — Build Lambda layers for Python deps, ffmpeg, and assets
# Run this from the project root before `terraform apply`
# Requires: Docker (to match Lambda's Amazon Linux 2023 environment)

set -e
LAYERS_DIR="infrastructure/layers"
mkdir -p "$LAYERS_DIR"

echo "════════════════════════════════════════"
echo "  Building Python dependencies layer"
echo "════════════════════════════════════════"
docker run --rm \
  -v "$(pwd)/$LAYERS_DIR:/out" \
  public.ecr.aws/lambda/python:3.12 \
  bash -c "
    pip install \
      google-api-python-client \
      google-auth-httplib2 \
      google-auth-oauthlib \
      anthropic \
      Pillow \
      pyttsx3 \
      -t /tmp/python/lib/python3.12/site-packages/ && \
    rm -rf /tmp/python/lib/python3.12/site-packages/googleapiclient/discovery_cache && \
    cd /tmp && zip -r9 /out/python_deps.zip python/
  "
echo "✅  python_deps.zip created"

echo ""
echo "════════════════════════════════════════"
echo "  Building ffmpeg layer"
echo "════════════════════════════════════════"
mkdir -p /tmp/ffmpeg_layer/bin
# Download static ffmpeg build for Amazon Linux 2023
curl -L "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz" \
  -o /tmp/ffmpeg.tar.xz
tar -xf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg_extract/
cp /tmp/ffmpeg_extract/*/ffmpeg /tmp/ffmpeg_layer/bin/
cp /tmp/ffmpeg_extract/*/ffprobe /tmp/ffmpeg_layer/bin/
# Add espeak
docker run --rm \
  -v "/tmp/ffmpeg_layer:/out" \
  public.ecr.aws/lambda/python:3.12 \
  bash -c "
    yum install -y espeak && \
    cp /usr/bin/espeak /out/bin/ && \
    cp -r /usr/lib/espeak /out/lib/ 2>/dev/null || true
  "
cd /tmp/ffmpeg_layer && zip -r9 "$(pwd)/$LAYERS_DIR/ffmpeg.zip" .
echo "✅  ffmpeg.zip created"

echo ""
echo "════════════════════════════════════════"
echo "  Building assets layer"
echo "════════════════════════════════════════"
# You must populate these directories manually before running:
#   assets/fonts/   — Nunito-Bold.ttf (free from Google Fonts)
#   assets/audio/   — rain*.mp3 files (royalty-free, e.g. freesound.org)
#   assets/nature/  — short jungle rain .mp4 clips (royalty-free, e.g. pixabay.com)
if [ -d "assets" ]; then
  mkdir -p /tmp/assets_layer/opt
  cp -r assets /tmp/assets_layer/opt/
  cd /tmp/assets_layer && zip -r9 "$(pwd)/$LAYERS_DIR/assets.zip" .
  echo "✅  assets.zip created"
else
  echo "⚠️   'assets/' directory not found."
  echo "     Create it with fonts, audio, and nature clips, then re-run."
  touch "$LAYERS_DIR/assets.zip"   # placeholder so Terraform doesn't fail
fi

echo ""
echo "🎉  All layers built in $LAYERS_DIR"
echo ""
echo "Next steps:"
echo "  1. Run: python scripts/get_youtube_token.py"
echo "  2. Store credentials in AWS Secrets Manager"
echo "  3. cd infrastructure && terraform init && terraform apply"
