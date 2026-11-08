
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

MIN_LIKE_SCORE = 75
EXCLUDED_MAX_SCORE = 30

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

        prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()

        if not prompt:
            raise ValueError("Like prompt file is empty")

        return prompt

    def evaluate_posts(self, posts):
        if not posts:
            return {"evaluations": []}

        self._validate_input_posts(posts)

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

    def _validate_input_posts(self, posts):
        if not isinstance(posts, list):
            raise ValueError(
                "Invalid Like input: expected a list of posts"
            )

        post_ids = []

        for post in posts:
            if not isinstance(post, dict):
                raise ValueError(
                    "Invalid Like input: every post must be an object"
                )

            post_id = post.get("id")

            if post_id is None or not str(post_id).strip():
                raise ValueError(
                    "Invalid Like input: post without a valid ID"
                )

            post_ids.append(str(post_id))

        if len(post_ids) != len(set(post_ids)):
            raise ValueError(
                "Invalid Like input: duplicate post IDs"
            )

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

            category = item.get("category")

            if category not in ALLOWED_CATEGORIES:
                raise ValueError(
                    f"Invalid category for post {post_id}"
                )

            reason = item.get("reason")

            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(
                    f"Missing reason for post {post_id}"
                )

            if should_like != (score >= MIN_LIKE_SCORE):
                raise ValueError(
                    f"Inconsistent Like decision for post {post_id}"
                )

            if category == "excluded_content":
                if should_like:
                    raise ValueError(
                        f"Excluded content cannot receive Like: {post_id}"
                    )

                if score > EXCLUDED_MAX_SCORE:
                    raise ValueError(
                        f"Excluded content score exceeds maximum: {post_id}"
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
