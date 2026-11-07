
import json
import os

from pathlib import Path
from google import genai
from google.genai import types


PROMPT_FILE = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "x_reply.txt"
)


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

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.8,
                response_mime_type="application/json"
            )
        )

        if not response.text:
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

        return reply
