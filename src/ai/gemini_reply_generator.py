
import json
import os
import re
import time

from pathlib import Path
from google import genai
from google.genai import types


PROMPT_FILE = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "x_reply.txt"
)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 30

LANGUAGE_CODE_PATTERN = re.compile(r"^[a-z]{2,3}$")


class GeminiReplyGenerator:

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
                f"Reply prompt not found: {PROMPT_FILE}"
            )

        return PROMPT_FILE.read_text(encoding="utf-8")

    def _generate_reply_once(self, contents):
        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.8,
                response_mime_type="application/json"
            )
        )

        if not response or not response.text:
            raise RuntimeError(
                "Gemini returned an empty response"
            )

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError as error:
            raise ValueError(
                "Gemini returned invalid JSON"
            ) from error

        if not isinstance(result, dict):
            raise ValueError(
                "Invalid reply response: expected an object"
            )

        reply = result.get("reply")

        if not isinstance(reply, str):
            raise ValueError(
                "Invalid reply: expected a string"
            )

        reply = reply.strip()

        # An empty reply means Gemini decided not to respond.
        if not reply:
            return ""

        if len(reply) > 280:
            raise ValueError(
                f"Reply exceeds 280 characters: {len(reply)}"
            )

        source_language = result.get("source_language")
        reply_language = result.get("reply_language")

        if not isinstance(source_language, str):
            raise ValueError(
                "Gemini did not return the detected source language"
            )

        if not isinstance(reply_language, str):
            raise ValueError(
                "Gemini did not return the reply language"
            )

        source_language = source_language.strip().lower()
        reply_language = reply_language.strip().lower()

        if not LANGUAGE_CODE_PATTERN.fullmatch(source_language):
            raise ValueError(
                f"Invalid source language code: {source_language}"
            )

        if not LANGUAGE_CODE_PATTERN.fullmatch(reply_language):
            raise ValueError(
                f"Invalid reply language code: {reply_language}"
            )

        if source_language != reply_language:
            raise ValueError(
                "Reply language mismatch: "
                f"source={source_language}, "
                f"reply={reply_language}"
            )

        return reply

    def generate_reply(self, post, ranking_item=None):
        if not isinstance(post, dict):
            raise ValueError("Invalid post")

        post_id = str(post.get("id", "")).strip()
        post_text = post.get("text", "").strip()

        if not post_id:
            raise ValueError("Post ID is missing")

        if not post_text:
            raise ValueError("Post text is empty")

        context = {
            "post_id": post_id,
            "text": post_text,
            "url": post.get("url"),
            "created_at": post.get("created_at"),
            "analysis": ranking_item or {}
        }

        contents = f"""
{self._load_prompt()}

PUBLICACIÓN A RESPONDER:

{json.dumps(context, ensure_ascii=False, indent=2)}
"""

        total_attempts = MAX_RETRIES + 1
        last_error = None

        for attempt in range(1, total_attempts + 1):
            print(
                f"[X REPLY] Calling Gemini. "
                f"Attempt: {attempt}/{total_attempts}. "
                f"Post ID: {post_id}"
            )

            try:
                reply = self._generate_reply_once(contents)

                if not reply:
                    print(
                        f"[X REPLY] Gemini decided not to reply. "
                        f"Post ID: {post_id}"
                    )
                    return ""

                print(
                    f"[X REPLY] Gemini reply generated successfully. "
                    f"Post ID: {post_id}"
                )

                return reply

            except Exception as error:
                last_error = error

                if attempt <= MAX_RETRIES:
                    retry_number = attempt

                    print(
                        f"[X REPLY] Reintento {retry_number}/{MAX_RETRIES}. "
                        f"Post ID: {post_id}. "
                        f"Error: {type(error).__name__}: {error}. "
                        f"Esperando {RETRY_DELAY_SECONDS} segundos."
                    )

                    time.sleep(RETRY_DELAY_SECONDS)

                else:
                    print(
                        f"[X REPLY] ERROR FINAL. "
                        f"Post ID: {post_id}. "
                        f"Se agotaron los {MAX_RETRIES} reintentos. "
                        f"Último error: {type(error).__name__}: {error}"
                    )

        raise last_error
