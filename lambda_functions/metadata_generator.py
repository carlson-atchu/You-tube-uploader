"""
MetadataGenerator — Calls Claude to write YouTube title, description, and tags.
"""

import json
import logging
import anthropic

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert YouTube content strategist specializing in:
1. Kids educational content (ages 3–10) — fun, safe, encouraging, SEO-optimized
2. Nature relaxation videos — calming, descriptive, search-friendly

Your job: given a video topic and category, generate:
- A compelling YouTube title (max 70 chars)
- A rich description (150–300 words) with relevant keywords naturally woven in
- 15–20 relevant tags as a JSON array of strings

Always respond with ONLY a JSON object in this exact format:
{
  "title": "...",
  "description": "...",
  "tags": ["tag1", "tag2", ...]
}

Rules for kids content:
- Title should be exciting and clear (e.g. "Why Is the Sky Blue? | Fun Science for Kids!")
- Description must be parent-friendly, mention educational value, include a call-to-subscribe
- Tags: mix broad (kids learning, education) and specific (sky, science for kids)

Rules for nature relaxation:
- Title should be atmospheric (e.g. "🌧️ Amazon Rainforest Rain — 3 Hours of Jungle Sounds")
- Description should set a calming scene, mention use cases (sleep, study, meditation)
- Tags: mix ambient, sleep sounds, nature ASMR, specific location terms
"""


class MetadataGenerator:
    def __init__(self, api_key: str, category: str):
        self.client   = anthropic.Anthropic(api_key=api_key)
        self.category = category

    def generate(self, video_key: str, topic: str, source: str) -> dict:
        """Generate metadata for a video. Falls back to defaults on error."""
        category_label = (
            "kids educational content" if self.category == "kids_learning"
            else "nature relaxation / ambient sound video"
        )

        prompt = (
            f"Category: {category_label}\n"
            f"Topic / Subject: {topic or video_key}\n"
            f"Source: {'AI-generated' if source == 'generated' else 'pre-recorded'}\n\n"
            "Generate the YouTube metadata JSON now."
        )

        try:
            message = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = message.content[0].text.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            metadata = json.loads(raw.strip())
        except Exception as e:
            logger.error("Metadata generation failed: %s. Using fallback.", e)
            metadata = self._fallback_metadata(topic)

        # Add YouTube category ID
        metadata["yt_category_id"] = (
            "27" if self.category == "kids_learning" else "15"
            # 27 = Education, 15 = Pets & Animals / Nature (closest for relaxation)
        )
        return metadata

    def _fallback_metadata(self, topic: str) -> dict:
        if self.category == "kids_learning":
            return {
                "title":       f"{topic} | Learning Video for Kids",
                "description": f"Join us as we explore {topic}! A fun and educational video for children.",
                "tags":        ["kids", "learning", "education", "children", topic.lower()],
            }
        return {
            "title":       f"{topic} | Relaxing Nature Sounds",
            "description": f"Enjoy the peaceful sounds of {topic}. Perfect for sleep, study, or meditation.",
            "tags":        ["nature sounds", "relaxation", "ambient", "sleep", topic.lower()],
        }
