
import json
import os
import logging
import time

from pathlib import Path
from google import genai
from google.genai import types


logger = logging.getLogger(__name__)

PROMPT_FILE = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "x_ranking.txt"
)

ALLOWED_CATEGORIES = {
    "official_news",
    "update",
    "community",
    "creator",
    "discussion",
    "gameplay",
    "event",
    "other"
}

ALLOWED_PRIORITIES = {
    "high",
    "medium",
    "low"
}

ALLOWED_ACTIONS = {
    "reply",
    "repost",
    "ignore"
}

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
RETRY_DELAYS = (10, 20)


class GeminiAnalysisError(RuntimeError):
    pass


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

    def _get_status_code(self, error):
        status_code = (
            getattr(error, "status_code", None)
            or getattr(error, "code", None)
        )

        try:
            return int(status_code)
        except (TypeError, ValueError):
            return None

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

        response = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                logger.info(
                    "Gemini analysis attempt %s/%s. Model: %s. Posts: %s",
                    attempt,
                    MAX_ATTEMPTS,
                    self.model,
                    len(posts)
                )

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        response_mime_type="application/json"
                    )
                )

                break

            except Exception as error:
                status_code = self._get_status_code(error)

                retryable = status_code in RETRYABLE_STATUS_CODES

                if not retryable or attempt >= MAX_ATTEMPTS:
                    logger.exception(
                        "Gemini API request failed. "
                        "Model: %s. Status: %s. Attempt: %s/%s",
                        self.model,
                        status_code,
                        attempt,
                        MAX_ATTEMPTS
                    )

                    raise GeminiAnalysisError(
                        f"Gemini API failed after {attempt} attempt(s). "
                        f"Status: {status_code}. "
                        "No ranking was generated."
                    ) from error

                delay = RETRY_DELAYS[attempt - 1]

                logger.warning(
                    "Gemini returned status %s. "
                    "Retrying in %s seconds.",
                    status_code,
                    delay
                )

                time.sleep(delay)

        if response is None or not response.text:
            raise GeminiAnalysisError(
                "Gemini returned an empty response"
            )

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError as error:
            raise GeminiAnalysisError(
                "Gemini returned invalid JSON"
            ) from error

        try:
            return self._validate_result(result, posts)
        except (ValueError, TypeError, KeyError) as error:
            raise GeminiAnalysisError(
                f"Gemini returned an invalid ranking: {error}"
            ) from error

    def _validate_result(self, result, posts):
        if not isinstance(result, dict):
            raise ValueError(
                "Invalid Gemini response: expected an object"
            )

        ranking = result.get("ranking")

        if not isinstance(ranking, list):
            raise ValueError(
                "Invalid Gemini response: missing ranking list"
            )

        valid_ids = {
            str(post["id"])
            for post in posts
        }

        processed_ids = set()

        for item in ranking:

            if not isinstance(item, dict):
                raise ValueError(
                    "Invalid ranking item: expected an object"
                )

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

            if item.get("category") not in ALLOWED_CATEGORIES:
                raise ValueError(
                    f"Invalid category for post {post_id}"
                )

            if item.get("priority") not in ALLOWED_PRIORITIES:
                raise ValueError(
                    f"Invalid priority for post {post_id}"
                )

            if item.get("recommended_action") not in ALLOWED_ACTIONS:
                raise ValueError(
                    f"Invalid action for post {post_id}"
                )

            reason = item.get("reason")

            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(
                    f"Missing reason for post {post_id}"
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

        return {
            "ranking": ranking
        }
