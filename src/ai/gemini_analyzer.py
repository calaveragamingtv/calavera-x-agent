
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

BATCH_SIZE = 10
MAX_RETRIES = 3
RETRY_DELAY = 30


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

    def analyze_posts(self, posts):
        if not posts:
            return {"ranking": []}

        prompt = self._load_prompt()

        batches = [
            posts[index:index + BATCH_SIZE]
            for index in range(0, len(posts), BATCH_SIZE)
        ]

        logger.info(
            "[X ANALYZER] Starting analysis. "
            "Total posts: %s. Batch size: %s. Total batches: %s",
            len(posts),
            BATCH_SIZE,
            len(batches)
        )

        combined_ranking = []

        for batch_number, batch in enumerate(batches, start=1):
            logger.info(
                "[X ANALYZER] Processing batch %s/%s. Posts: %s",
                batch_number,
                len(batches),
                len(batch)
            )

            batch_result = self._analyze_batch(
                prompt=prompt,
                posts=batch,
                batch_number=batch_number,
                total_batches=len(batches)
            )

            combined_ranking.extend(batch_result["ranking"])

        combined_ranking.sort(
            key=lambda item: item["score"],
            reverse=True
        )

        logger.info(
            "[X ANALYZER] All batches completed successfully. "
            "Total analyzed posts: %s",
            len(combined_ranking)
        )

        return {
            "ranking": combined_ranking
        }

    def _analyze_batch(
        self,
        prompt,
        posts,
        batch_number,
        total_batches
    ):
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

        last_error = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(
                    "[X ANALYZER] Calling Gemini. "
                    "Batch: %s/%s. Attempt: %s/%s. "
                    "Model: %s. Posts: %s",
                    batch_number,
                    total_batches,
                    attempt + 1,
                    MAX_RETRIES + 1,
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
                    validated_result = self._validate_result(
                        result,
                        posts
                    )
                except (ValueError, TypeError, KeyError) as error:
                    raise GeminiAnalysisError(
                        f"Gemini returned an invalid ranking: {error}"
                    ) from error

                logger.info(
                    "[X ANALYZER] Batch completed successfully. "
                    "Batch: %s/%s. Posts: %s",
                    batch_number,
                    total_batches,
                    len(posts)
                )

                return validated_result

            except Exception as error:
                last_error = error

                if attempt < MAX_RETRIES:
                    retry_number = attempt + 1

                    logger.warning(
                        "[X ANALYZER] Batch %s/%s. "
                        "Reintento %s/%s. Waiting %s seconds. "
                        "Error: %s",
                        batch_number,
                        total_batches,
                        retry_number,
                        MAX_RETRIES,
                        RETRY_DELAY,
                        error,
                        exc_info=True
                    )

                    time.sleep(RETRY_DELAY)

                else:
                    logger.exception(
                        "[X ANALYZER] Batch %s/%s failed after "
                        "%s attempts. Final error: %s",
                        batch_number,
                        total_batches,
                        MAX_RETRIES + 1,
                        error
                    )

        raise GeminiAnalysisError(
            f"Analysis failed for batch {batch_number}/{total_batches}: "
            f"{last_error}"
        ) from last_error

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

MAX_RETRIES = 3
RETRY_DELAY = 30


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

        last_error = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(
                    "[X ANALYZER] Calling Gemini. "
                    "Attempt: %s/%s. Model: %s. Posts: %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
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
                    validated_result = self._validate_result(
                        result,
                        posts
                    )
                except (ValueError, TypeError, KeyError) as error:
                    raise GeminiAnalysisError(
                        f"Gemini returned an invalid ranking: {error}"
                    ) from error

                logger.info(
                    "[X ANALYZER] Analysis completed successfully. "
                    "Posts: %s",
                    len(posts)
                )

                return validated_result

            except Exception as error:
                last_error = error

                if attempt < MAX_RETRIES:
                    retry_number = attempt + 1

                    logger.warning(
                        "[X ANALYZER] Reintento %s/%s. "
                        "Waiting %s seconds. Error: %s",
                        retry_number,
                        MAX_RETRIES,
                        RETRY_DELAY,
                        error,
                        exc_info=True
                    )

                    time.sleep(RETRY_DELAY)

                else:
                    logger.exception(
                        "[X ANALYZER] Fallaron los %s reintentos. "
                        "Error final: %s",
                        MAX_RETRIES,
                        error
                    )

        raise last_error

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
