
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


class PostTracker:
    ACTIONS = ("like", "reply", "repost")
    ACTION_STATUSES = (
        "not_started",
        "processing",
        "completed",
        "failed",
        "unknown",
    )

    def __init__(self, file_path: str | Path = "data/processed_posts.json"):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {
                "schema_version": 1,
                "posts": {}
            }
    
        try:
            with self.file_path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid tracker JSON: {self.file_path}"
            ) from exc
    
        if not isinstance(data, dict):
            raise RuntimeError("Invalid processed_posts.json structure")
    
        # Already using the new structure
        if isinstance(data.get("posts"), dict):
            return data
    
        # Migrate legacy structure:
        # {
        #   "post_id": {
        #       "text": "...",
        #       "analysis": {...},
        #       "actions": {...}
        #   }
        # }
        migrated_posts = {}
    
        for post_id, old_post in data.items():
            if not isinstance(old_post, dict):
                raise RuntimeError(
                    f"Invalid legacy post structure: {post_id}"
                )
    
            old_analysis = old_post.get("analysis", {})
            old_actions = old_post.get("actions", {})
    
            if not isinstance(old_analysis, dict):
                old_analysis = {}
    
            if not isinstance(old_actions, dict):
                old_actions = {}
    
            migrated_actions = {}
    
            for action in self.ACTIONS:
                old_action = old_actions.get(action, {})
    
                if not isinstance(old_action, dict):
                    old_action = {}
    
                migrated_actions[action] = {
                    "status": old_action.get("status", "not_started"),
                    "attempts": old_action.get("attempts", 0),
                    "last_attempt_at": old_action.get("last_attempt_at"),
                    "completed_at": old_action.get("completed_at"),
                    "error": old_action.get("error"),
                    "external_id": old_action.get("external_id")
                }
    
            analysis_status = old_analysis.get("status", "pending")
    
            migrated_analysis = {
                "status": analysis_status,
                "result": old_analysis.get("result")
            }
    
            # Preserve results from the previous tracker format.
            if analysis_status == "completed" and not migrated_analysis["result"]:
                migrated_analysis["legacy_result"] = old_analysis.copy()
    
            migrated_posts[str(post_id)] = {
                "id": str(post_id),
                "text": old_post.get("text", ""),
                "url": old_post.get("url"),
                "created_at": old_post.get("created_at"),
                "first_seen_at": old_post.get("first_seen_at", self._now()),
                "analysis": migrated_analysis,
                "actions": migrated_actions
            }
    
        migrated_data = {
            "schema_version": 1,
            "posts": migrated_posts
        }
    
        print(
            f"Migrated {len(migrated_posts)} posts "
            "from legacy tracker format"
        )
    
        self.data = migrated_data
        self._save()
    
        return migrated_data


    def _save(self) -> None:
        temporary_file = self.file_path.with_suffix(".tmp")

        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(
                self.data,
                file,
                ensure_ascii=False,
                indent=2
            )
            file.flush()
            os.fsync(file.fileno())

        temporary_file.replace(self.file_path)

    def register_posts(self, posts: list[dict]) -> list[str]:
        """
        Registers new posts.
        Returns the IDs that were not previously registered.
        """
        new_ids = []

        for post in posts:
            post_id = str(post.get("id", "")).strip()

            if not post_id:
                raise ValueError("A post without ID cannot be registered")

            if post_id in self.data["posts"]:
                continue

            text = post.get("text") or post.get("fullText") or ""

            self.data["posts"][post_id] = {
                "id": post_id,
                "text": text,
                "url": post.get("url"),
                "created_at": post.get("created_at") or post.get("createdAt"),
                "first_seen_at": self._now(),

                "analysis": {
                    "status": "pending",
                    "result": None
                },

                "actions": {
                    action: {
                        "status": "not_started",
                        "attempts": 0,
                        "last_attempt_at": None,
                        "completed_at": None,
                        "error": None,
                        "external_id": None
                    }
                    for action in self.ACTIONS
                }
            }

            new_ids.append(post_id)

        if new_ids:
            self._save()

        return new_ids

    def mark_analysis_completed(
        self,
        post_id: str,
        result: dict
    ) -> None:
        post = self._get_post(post_id)

        post["analysis"] = {
            "status": "completed",
            "result": result,
            "completed_at": self._now()
        }

        self._save()

    def mark_analysis_failed(
        self,
        post_id: str,
        error: str
    ) -> None:
        post = self._get_post(post_id)

        post["analysis"] = {
            "status": "failed",
            "error": error,
            "last_attempt_at": self._now()
        }

        self._save()

    def mark_action_started(
        self,
        post_id: str,
        action: str
    ) -> None:
        self._validate_action(action)
        post = self._get_post(post_id)
        action_data = post["actions"][action]

        if action_data["status"] not in ("not_started", "failed"):
            raise RuntimeError(
                f"Cannot start {action} for {post_id}: "
                f"current status is {action_data['status']}"
            )

        action_data["status"] = "processing"
        action_data["attempts"] += 1
        action_data["last_attempt_at"] = self._now()
        action_data["error"] = None

        self._save()

    def mark_action_result(
        self,
        post_id: str,
        action: str,
        status: str,
        error: str | None = None,
        external_id: str | None = None
    ) -> None:
        self._validate_action(action)

        if status not in ("completed", "failed", "unknown"):
            raise ValueError(f"Invalid action result: {status}")

        post = self._get_post(post_id)
        action_data = post["actions"][action]

        if action_data["status"] != "processing":
            raise RuntimeError(
                f"Cannot finish {action} for {post_id}: "
                f"current status is {action_data['status']}"
            )

        action_data["status"] = status
        action_data["error"] = error
        action_data["external_id"] = external_id

        if status == "completed":
            action_data["completed_at"] = self._now()

        self._save()

    def get_post(self, post_id: str) -> dict | None:
        return self.data["posts"].get(str(post_id))

    def get_unanalyzed_ids(self) -> list[str]:
        return [
            post_id
            for post_id, post in self.data["posts"].items()
            if post["analysis"]["status"] in ("pending", "failed")
        ]

    def get_pending_action_ids(self, action: str) -> list[str]:
        self._validate_action(action)

        return [
            post_id
            for post_id, post in self.data["posts"].items()
            if post["actions"][action]["status"] in ("not_started", "failed")
        ]

    def _get_post(self, post_id: str) -> dict:
        post = self.get_post(post_id)

        if post is None:
            raise KeyError(f"Post not registered: {post_id}")

        return post

    def _validate_action(self, action: str) -> None:
        if action not in self.ACTIONS:
            raise ValueError(f"Unsupported action: {action}")
