
import json
import os

from pathlib import Path
from google import genai
from google.genai import types


PROMPT_FILE = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "x_like.txt"
)

ALLOWED_CATEGORIES = {
    "streaming",
    "youtube",
    "kick",
    "twitch",
    "gaming",
    "rust",
    "technology",
    "news",
    "entertainment",
    "community",
    "humor",
    "professional",
    "other",
    "excluded_content"
}


class GeminiLikeEvaluator:

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
                f"Like prompt file not found: {PROMPT_FILE}"
            )

        return PROMPT_FILE.read_text(encoding="utf-8")

    def evaluate_posts(self, posts):
        if not posts:
            return {"evaluations": []}

        prompt = self._load_prompt()

        posts_json = json.dumps(
            posts,
            ensure_ascii=False,
            indent=2
        )

        contents = f"""
{prompt}

PUBLICACIONES A EVALUAR:

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
            raise RuntimeError(
                "Gemini Like Evaluator returned an empty response"
            )

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError as error:
            raise ValueError(
                "Gemini Like Evaluator returned invalid JSON"
            ) from error

        return self._validate_result(result, posts)

    def _validate_result(self, result, posts):
        if not isinstance(result, dict):
            raise ValueError(
                "Invalid Like evaluation: expected an object"
            )

        evaluations = result.get("evaluations")

        if not isinstance(evaluations, list):
            raise ValueError(
                "Invalid Like evaluation: missing evaluations list"
            )

        valid_ids = {
            str(post["id"])
            for post in posts
        }

        processed_ids = set()

        for item in evaluations:

            if not isinstance(item, dict):
                raise ValueError(
                    "Invalid Like evaluation item: expected an object"
                )

            post_id = str(item.get("post_id", ""))

            if post_id not in valid_ids:
                raise ValueError(
                    f"Gemini returned unknown post ID: {post_id}"
                )

            if post_id in processed_ids:
                raise ValueError(
                    f"Duplicate Like evaluation for post: {post_id}"
                )

            processed_ids.add(post_id)

            should_like = item.get("should_like")

            if not isinstance(should_like, bool):
                raise ValueError(
                    f"Invalid should_like for post {post_id}"
                )

            score = item.get("score")

            if (
                not isinstance(score, int)
                or isinstance(score, bool)
                or not 0 <= score <= 100
            ):
                raise ValueError(
                    f"Invalid score for post {post_id}"
                )

            if item.get("category") not in ALLOWED_CATEGORIES:
                raise ValueError(
                    f"Invalid category for post {post_id}"
                )

            reason = item.get("reason")

            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(
                    f"Missing reason for post {post_id}"
                )

            if should_like != (score >= 75):
                raise ValueError(
                    f"Inconsistent Like decision for post {post_id}"
                )

        if processed_ids != valid_ids:
            missing = valid_ids - processed_ids

            raise ValueError(
                f"Gemini omitted Like evaluations: {sorted(missing)}"
            )

        evaluations.sort(
            key=lambda item: item["score"],
            reverse=True
        )

        return {
            "evaluations": evaluations
        }
