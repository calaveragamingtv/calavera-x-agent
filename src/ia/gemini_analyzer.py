import json
import os

from pathlib import Path
from google import genai
from google.genai import types


PROMPT_FILE = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "x_ranking.txt"
)


class GeminiAnalyzer:

    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")

        if not api_key:
            raise RuntimeError("Missing GEMINI_API_KEY")

        self.model = os.environ.get(
            "GEMINI_MODEL",
            "gemini-3.5-flash-lite"
        )

        self.client = genai.Client(api_key=api_key)

    def _load_prompt(self):
        if not PROMPT_FILE.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {PROMPT_FILE}"
            )

        return PROMPT_FILE.read_text(encoding="utf-8")

    def analyze_posts(self, posts):
        if not posts:
            return {"ranking": []}

        prompt = self._load_prompt()

        posts_json = json.dumps(
            posts,
            ensure_ascii=False,
            indent=2
        )

        contents = f"""
{prompt}

PUBLICACIONES A ANALIZAR:

{posts_json}
"""

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json"
            )
        )

        if not response.text:
            raise RuntimeError("Gemini returned an empty response")

        result = json.loads(response.text)

        return self._validate_result(result, posts)

    def _validate_result(self, result, posts):
        if not isinstance(result, dict):
            raise ValueError("Invalid Gemini response")

        ranking = result.get("ranking")

        if not isinstance(ranking, list):
            raise ValueError("Missing ranking list")

        valid_ids = {
            str(post["id"])
            for post in posts
        }

        processed_ids = set()

        for item in ranking:
            post_id = str(item.get("post_id", ""))

            if post_id not in valid_ids:
                raise ValueError(
                    f"Gemini returned unknown post ID: {post_id}"
                )

            if post_id in processed_ids:
                raise ValueError(
                    f"Duplicate post ID: {post_id}"
                )

            processed_ids.add(post_id)

            score = item.get("score")

            if (
                not isinstance(score, int)
                or isinstance(score, bool)
                or not 0 <= score <= 100
            ):
                raise ValueError(
                    f"Invalid score for post {post_id}"
                )

        if processed_ids != valid_ids:
            missing = valid_ids - processed_ids

            raise ValueError(
                f"Gemini omitted posts: {sorted(missing)}"
            )

        ranking.sort(
            key=lambda item: item["score"],
            reverse=True
        )

        return {"ranking": ranking}
