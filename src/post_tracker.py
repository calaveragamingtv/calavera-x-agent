import json
import os

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


class PostTracker:
    ACTIONS = ("like", "reply", "repost")

    ACTION_STATUSES = (
        "not_started",
        "drafted",
        "processing",
        "completed",
        "failed",
        "unknown",
    )

    # An action consumes its daily allowance once a request may have been
    # sent. Unknown results also consume quota to prevent accidental retries.
    CONSUMING_STATUSES = ("processing", "completed", "unknown")

    def __init__(
        self,
        file_path: str | Path = "data/processed_posts.json",
        config_path: str | Path = "config/x_actions.json",
    ):
        self.file_path = Path(file_path)
        self.config_path = Path(config_path)

        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        self.config = self._load_config()
        self.timezone = ZoneInfo(self.config["timezone"])

        self.data = self._load()

    def _load_config(self) -> dict:
        """Load and validate the centralized X actions configuration."""

        if not self.config_path.exists():
            raise FileNotFoundError(
                f"X actions configuration not found: {self.config_path}"
            )

        try:
            with self.config_path.open("r", encoding="utf-8") as file:
                config = json.load(file)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid X actions configuration: {self.config_path}"
            ) from exc

        if not isinstance(config, dict):
            raise RuntimeError("Invalid x_actions.json structure")

        actions = config.get("actions")
        limits = config.get("limits")
        scraping = config.get("scraping")
        timezone_name = config.get("timezone")

        if not isinstance(actions, dict):
            raise RuntimeError(
                "Missing or invalid 'actions' configuration"
            )

        if not isinstance(limits, dict):
            raise RuntimeError(
                "Missing or invalid 'limits' configuration"
            )

        if not isinstance(scraping, dict):
            raise RuntimeError(
                "Missing or invalid 'scraping' configuration"
            )

        if (
            not isinstance(timezone_name, str)
            or not timezone_name.strip()
        ):
            raise RuntimeError(
                "Missing or invalid timezone configuration"
            )

        try:
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise RuntimeError(
                f"Invalid timezone in x_actions.json: {timezone_name}"
            ) from exc

        for action in self.ACTIONS:
            action_config = actions.get(action)

            if not isinstance(action_config, dict):
                raise RuntimeError(
                    f"Missing actions configuration for: {action}"
                )

            if not isinstance(action_config.get("enabled"), bool):
                raise RuntimeError(
                    f"Invalid {action}.enabled configuration"
                )

        for action in self.ACTIONS:
            action_limits = limits.get(action)

            if not isinstance(action_limits, dict):
                raise RuntimeError(
                    f"Missing limits configuration for action: {action}"
                )

            max_per_day = action_limits.get("max_per_day")

            if (
                not isinstance(max_per_day, int)
                or isinstance(max_per_day, bool)
                or max_per_day < 0
            ):
                raise RuntimeError(
                    f"Invalid max_per_day for action: {action}"
                )

        min_like_score = limits["like"].get("min_score")

        if (
            not isinstance(min_like_score, int)
            or isinstance(min_like_score, bool)
            or not 0 <= min_like_score <= 100
        ):
            raise RuntimeError(
                "Invalid like.min_score configuration (expected 0-100)"
            )

        # Validate Like per-author limit.
        max_likes_per_author = limits["like"].get(
            "max_per_author_per_day"
        )

        if (
            not isinstance(max_likes_per_author, int)
            or isinstance(max_likes_per_author, bool)
            or max_likes_per_author < 0
        ):
            raise RuntimeError(
                "Invalid like.max_per_author_per_day configuration"
            )

        max_replies_per_author = limits["reply"].get(
            "max_per_author_per_day"
        )

        if (
            not isinstance(max_replies_per_author, int)
            or isinstance(max_replies_per_author, bool)
            or max_replies_per_author < 0
        ):
            raise RuntimeError(
                "Invalid reply.max_per_author_per_day configuration"
            )

        posts_per_account = scraping.get("posts_per_account")

        if (
            not isinstance(posts_per_account, int)
            or isinstance(posts_per_account, bool)
            or posts_per_account <= 0
        ):
            raise RuntimeError(
                "Invalid scraping.posts_per_account configuration"
            )

        print("X actions configuration loaded successfully")
        print(f"Configured timezone: {timezone_name}")

        for action in self.ACTIONS:
            print(
                f"{action.capitalize()} enabled: "
                f"{actions[action]['enabled']}"
            )
            print(
                f"{action.capitalize()} daily limit: "
                f"{limits[action]['max_per_day']}"
            )

        print(f"Like minimum score: {min_like_score}")
        print(
            "Like per-author daily limit: "
            f"{max_likes_per_author}"
        )
        print(
            "Reply per-author daily limit: "
            f"{max_replies_per_author}"
        )
        print(f"Posts per account: {posts_per_account}")

        return config

    def get_action_limit(self, action: str) -> int:
        """Return the configured daily limit for an action."""

        self._validate_action(action)
        return self.config["limits"][action]["max_per_day"]

    def get_reply_author_limit(self) -> int:
        """Return the configured daily Reply limit per author."""

        return self.config["limits"]["reply"]["max_per_author_per_day"]

    def get_like_author_limit(self) -> int:
        """Return the configured daily Like limit per author."""

        return self.config["limits"]["like"]["max_per_author_per_day"]

    def get_like_min_score(self) -> int:
        """Return the configured minimum score required for a Like."""

        return self.config["limits"]["like"]["min_score"]

    def get_posts_per_account(self) -> int:
        """Return the configured number of posts per monitored account."""

        return self.config["scraping"]["posts_per_account"]

    def is_action_enabled(self, action: str) -> bool:
        """Return whether an action is enabled in the centralized config."""

        self._validate_action(action)
        return self.config["actions"][action]["enabled"]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {
                "schema_version": 1,
                "posts": {},
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

        if isinstance(data.get("posts"), dict):
            changed = False

            for post in data["posts"].values():
                if not isinstance(post, dict):
                    continue

                actions = post.get("actions")

                if not isinstance(actions, dict):
                    actions = {}
                    post["actions"] = actions
                    changed = True

                for action in self.ACTIONS:
                    action_data = actions.get(action)

                    if not isinstance(action_data, dict):
                        action_data = {}
                        actions[action] = action_data
                        changed = True

                    defaults = {
                        "status": "not_started",
                        "attempts": 0,
                        "last_attempt_at": None,
                        "completed_at": None,
                        "error": None,
                        "external_id": None,
                        "payload": None,
                    }

                    for key, value in defaults.items():
                        if key not in action_data:
                            action_data[key] = value
                            changed = True

                    if action_data["status"] not in self.ACTION_STATUSES:
                        raise RuntimeError(
                            f"Invalid action status: {action_data['status']}"
                        )

            if changed:
                self._save_data(data)

            return data

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

                status = old_action.get("status", "not_started")

                if status not in self.ACTION_STATUSES:
                    raise RuntimeError(
                        f"Invalid legacy {action} status for post {post_id}: "
                        f"{status}"
                    )

                migrated_actions[action] = {
                    "status": status,
                    "attempts": old_action.get("attempts", 0),
                    "last_attempt_at": old_action.get("last_attempt_at"),
                    "completed_at": old_action.get("completed_at"),
                    "error": old_action.get("error"),
                    "external_id": old_action.get("external_id"),
                    "payload": old_action.get("payload"),
                }

            analysis_status = old_analysis.get("status", "pending")

            migrated_analysis = {
                "status": analysis_status,
                "result": old_analysis.get("result"),
            }

            if (
                analysis_status == "completed"
                and not migrated_analysis["result"]
            ):
                migrated_analysis["legacy_result"] = old_analysis.copy()

            migrated_posts[str(post_id)] = {
                "id": str(post_id),
                "author_id": (
                    str(old_post["author_id"])
                    if old_post.get("author_id") is not None
                    else None
                ),
                "text": old_post.get("text", ""),
                "url": old_post.get("url"),
                "created_at": old_post.get("created_at"),
                "first_seen_at": old_post.get("first_seen_at", self._now()),
                "analysis": migrated_analysis,
                "actions": migrated_actions,
            }

        migrated_data = {
            "schema_version": 1,
            "posts": migrated_posts,
        }

        print(
            f"Migrated {len(migrated_posts)} posts "
            "from legacy tracker format"
        )

        self._save_data(migrated_data)
        return migrated_data

    def _save_data(self, data: dict) -> None:
        temporary_file = self.file_path.with_suffix(".tmp")

        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.flush()
            os.fsync(file.fileno())

        temporary_file.replace(self.file_path)

    def _save(self) -> None:
        self._save_data(self.data)

    @staticmethod
    def _post_metadata(post: dict) -> dict:
        """
        Extract optional metadata used by action evaluators.

        Different sources may expose counters under different names.
        Missing values are omitted so existing useful metadata is not erased.
        """
        aliases = {
            "author": ("author", "username", "author_username"),
            "likes": ("likes", "like_count", "likeCount"),
            "views": ("views", "view_count", "viewCount"),
            "replies": ("replies", "reply_count", "replyCount"),
            "reposts": ("reposts", "retweets", "repost_count", "retweetCount"),
            "content_type": ("content_type", "contentType"),
        }

        metadata = {}

        for target, candidates in aliases.items():
            for candidate in candidates:
                if candidate in post and post[candidate] is not None:
                    metadata[target] = post[candidate]
                    break

        return metadata

    @staticmethod
    def _has_metadata_value(value) -> bool:
        """Return whether a metadata value is useful and safe to persist."""
        if value is None:
            return False

        if isinstance(value, str):
            return bool(value.strip())

        return True

    def register_posts(self, posts: list[dict]) -> list[str]:
        new_ids = []
        changed = False

        for post in posts:
            post_id = str(post.get("id", "")).strip()

            if not post_id:
                raise ValueError("A post without ID cannot be registered")

            author_id = post.get("author_id")

            if author_id is not None:
                author_id = str(author_id)

            expanded_urls = post.get("expanded_urls", [])
            monitored_by = post.get("monitored_by", [])

            if not isinstance(expanded_urls, list):
                expanded_urls = []

            if not isinstance(monitored_by, list):
                monitored_by = []

            text = post.get("text") or post.get("fullText") or ""
            url = post.get("url")
            created_at = post.get("created_at") or post.get("createdAt")
            metadata = self._post_metadata(post)

            if post_id in self.data["posts"]:
                tracked_post = self.data["posts"][post_id]

                # Refresh source metadata without touching analysis or action
                # state. This keeps the tracker useful when a post re-enters
                # the active scraping window.
                refresh_values = {
                    "author_id": author_id,
                    "text": text,
                    "url": url,
                    "created_at": created_at,
                    "expanded_urls": expanded_urls,
                    "monitored_by": monitored_by,
                    **metadata,
                }

                for field, value in refresh_values.items():
                    if not self._has_metadata_value(value):
                        continue

                    if tracked_post.get(field) != value:
                        tracked_post[field] = value
                        changed = True

                continue

            tracked_post = {
                "id": post_id,
                "author_id": author_id,
                "text": text,
                "url": url,
                "created_at": created_at,
                "expanded_urls": expanded_urls,
                "monitored_by": monitored_by,
                "first_seen_at": self._now(),
                "analysis": {
                    "status": "pending",
                    "result": None,
                },
                "actions": {
                    action: {
                        "status": "not_started",
                        "attempts": 0,
                        "last_attempt_at": None,
                        "completed_at": None,
                        "error": None,
                        "external_id": None,
                        "payload": None,
                    }
                    for action in self.ACTIONS
                },
            }

            tracked_post.update(metadata)
            self.data["posts"][post_id] = tracked_post

            new_ids.append(post_id)
            changed = True

        if changed:
            self._save()

        return new_ids

    def mark_analysis_completed(
        self,
        post_id: str,
        result: dict,
    ) -> None:
        post = self._get_post(post_id)

        post["analysis"] = {
            "status": "completed",
            "result": result,
            "completed_at": self._now(),
        }

        self._save()

    def mark_analysis_failed(
        self,
        post_id: str,
        error: str,
    ) -> None:
        post = self._get_post(post_id)

        post["analysis"] = {
            "status": "failed",
            "error": error,
            "last_attempt_at": self._now(),
        }

        self._save()

    def migrate_legacy_reply_drafts(self) -> int:
        """
        Converts legacy reply drafts stored as processing into drafted.
        These were generated drafts, not actual publishing attempts.
        """
        migrated = 0

        for post in self.data["posts"].values():
            action = post.get("actions", {}).get("reply", {})

            if action.get("status") != "processing":
                continue

            payload = action.get("payload")

            if not isinstance(payload, dict):
                continue

            if payload.get("stage") is not None:
                continue

            payload["stage"] = "drafted"

            action["status"] = "drafted"
            action["attempts"] = 0
            action["last_attempt_at"] = None
            action["completed_at"] = None
            action["external_id"] = None
            action["error"] = None

            migrated += 1

        if migrated:
            self._save()

        return migrated

    def mark_action_drafted(
        self,
        post_id: str,
        action: str,
        payload: dict,
    ) -> None:
        self._validate_action(action)

        if not isinstance(payload, dict):
            raise ValueError("Action payload must be a dictionary")

        post = self._get_post(post_id)
        action_data = post["actions"][action]

        if action_data["status"] not in ("not_started", "failed"):
            raise RuntimeError(
                f"Cannot save {action} draft for {post_id}: "
                f"current status is {action_data['status']}"
            )

        saved_payload = dict(payload)
        saved_payload["stage"] = "drafted"

        action_data["status"] = "drafted"
        action_data["payload"] = saved_payload
        action_data["error"] = None

        self._save()

    def cancel_like_draft(
        self,
        post_id: str,
        reason: str = "No longer approved by current evaluation",
    ) -> bool:
        """
        Cancels an outdated Like draft.

        This method only affects Likes currently in drafted status.
        Replies and Reposts are never modified.
        """
        post = self.get_post(post_id)

        if post is None:
            return False

        like_action = post["actions"]["like"]

        if like_action["status"] != "drafted":
            return False

        like_action["status"] = "not_started"
        like_action["payload"] = None
        like_action["error"] = f"Like draft canceled: {reason}"
        like_action["external_id"] = None
        like_action["completed_at"] = None

        self._save()
        return True

    def mark_action_started(
        self,
        post_id: str,
        action: str,
        payload: dict | None = None,
    ) -> None:
        self._validate_action(action)

        post = self._get_post(post_id)
        action_data = post["actions"][action]

        if action_data["status"] not in (
            "not_started",
            "drafted",
            "failed",
        ):
            raise RuntimeError(
                f"Cannot start {action} for {post_id}: "
                f"current status is {action_data['status']}"
            )

        if payload is not None:
            if not isinstance(payload, dict):
                raise ValueError("Action payload must be a dictionary")

            saved_payload = dict(payload)
        else:
            saved_payload = action_data.get("payload")

        if isinstance(saved_payload, dict):
            saved_payload["stage"] = "sending"

        action_data["status"] = "processing"
        action_data["attempts"] += 1
        action_data["last_attempt_at"] = self._now()
        action_data["error"] = None
        action_data["external_id"] = None
        action_data["payload"] = saved_payload

        self._save()

    def mark_action_result(
        self,
        post_id: str,
        action: str,
        status: str,
        error: str | None = None,
        external_id: str | None = None,
    ) -> None:
        self._validate_action(action)

        if status not in ("completed", "failed", "unknown"):
            raise ValueError(f"Invalid action result: {status}")

        if status == "completed" and not external_id:
            raise ValueError(
                "Cannot mark action as completed without an external ID"
            )

        post = self._get_post(post_id)
        action_data = post["actions"][action]

        if action_data["status"] != "processing":
            raise RuntimeError(
                f"Cannot finish {action} for {post_id}: "
                f"current status is {action_data['status']}"
            )

        action_data["status"] = status
        action_data["error"] = error
        action_data["external_id"] = (
            str(external_id) if external_id is not None else None
        )

        payload = action_data.get("payload")

        if isinstance(payload, dict):
            payload["stage"] = status

        if status == "completed":
            action_data["completed_at"] = self._now()

        self._save()

    def recover_interrupted_actions(
        self,
        action: str | None = None,
    ) -> int:
        """
        Marks interrupted sending actions as unknown.
        Drafted actions are preserved.
        """
        if action is not None:
            self._validate_action(action)

        recovered = 0

        for post in self.data["posts"].values():
            actions = post.get("actions", {})

            selected_actions = (
                [action]
                if action is not None
                else self.ACTIONS
            )

            for action_name in selected_actions:
                action_data = actions.get(action_name, {})

                if action_data.get("status") != "processing":
                    continue

                payload = action_data.get("payload")

                if (
                    action_name == "reply"
                    and isinstance(payload, dict)
                    and payload.get("stage") == "drafted"
                ):
                    action_data["status"] = "drafted"
                    recovered += 1
                    continue

                action_data["status"] = "unknown"
                action_data["error"] = (
                    "Execution interrupted; publication result unknown"
                )

                if isinstance(payload, dict):
                    payload["stage"] = "unknown"

                recovered += 1

        if recovered:
            self._save()

        return recovered

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

    def get_drafted_action_ids(self, action: str) -> list[str]:
        self._validate_action(action)

        return [
            post_id
            for post_id, post in self.data["posts"].items()
            if post["actions"][action]["status"] == "drafted"
        ]

    def _get_post(self, post_id: str) -> dict:
        post = self.get_post(post_id)

        if post is None:
            raise KeyError(f"Post not registered: {post_id}")

        return post

    def _validate_action(self, action: str) -> None:
        if action not in self.ACTIONS:
            raise ValueError(f"Unsupported action: {action}")

    def _local_date(self, timestamp: str | None):
        """Convert a timestamp to a date using the configured timezone."""

        if not timestamp:
            return None

        try:
            parsed = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(self.timezone).date()
        except (ValueError, TypeError):
            return None

    def get_action_usage(self, action: str) -> dict:
        """
        Return today's usage for an action in the configured timezone.

        Processing and unknown attempts consume quota.
        Completed actions use their completion timestamp.
        """
        self._validate_action(action)

        today = datetime.now(self.timezone).date()
        total_today = 0

        for post in self.data["posts"].values():
            action_data = post.get("actions", {}).get(action, {})
            status = action_data.get("status")

            if status not in self.CONSUMING_STATUSES:
                continue

            timestamp = (
                action_data.get("completed_at")
                or action_data.get("last_attempt_at")
            )

            if self._local_date(timestamp) == today:
                total_today += 1

        return {
            "total_today": total_today,
            "today": today.isoformat(),
        }

    def can_perform_action_today(
        self,
        action: str,
        max_per_day: int | None = None,
    ) -> tuple[bool, str]:
        """
        Check whether an action can be performed today.

        max_per_day is retained for compatibility with older callers.
        The configured value in x_actions.json is authoritative.
        """
        self._validate_action(action)

        configured_limit = self.get_action_limit(action)
        usage = self.get_action_usage(action)

        if usage["total_today"] >= configured_limit:
            return False, "daily_action_limit_reached"

        return True, "allowed"

    def get_like_usage(self, author_id: str) -> dict:
        """Return today's total Like usage and usage for a specific author."""

        today = datetime.now(self.timezone).date()

        author_id = str(author_id)
        total_likes_today = 0
        author_likes_today = 0

        for post in self.data["posts"].values():
            action = post.get("actions", {}).get("like", {})
            status = action.get("status")

            if status not in self.CONSUMING_STATUSES:
                continue

            timestamp = (
                action.get("completed_at")
                or action.get("last_attempt_at")
            )

            if self._local_date(timestamp) != today:
                continue

            total_likes_today += 1

            if str(post.get("author_id") or "") == author_id:
                author_likes_today += 1

        return {
            "total_likes": total_likes_today,
            "total_likes_today": total_likes_today,
            "author_likes_today": author_likes_today,
            "today": today.isoformat(),
        }

    def can_like_to_author_today(
        self,
        author_id: str,
    ) -> tuple[bool, str]:
        """
        Check both the global daily Like limit and the per-author limit.
        Both configured values in x_actions.json are authoritative.
        """
        if not author_id:
            return False, "missing_author_id"

        usage = self.get_like_usage(author_id)

        configured_total_limit = self.get_action_limit("like")
        configured_author_limit = self.get_like_author_limit()

        if usage["total_likes_today"] >= configured_total_limit:
            return False, "daily_like_limit_reached"

        if usage["author_likes_today"] >= configured_author_limit:
            return False, "author_daily_like_limit_reached"

        return True, "allowed"

    def get_reply_usage(self, author_id: str) -> dict:
        """Return today's total Reply usage and usage for a specific author."""

        today = datetime.now(self.timezone).date()

        author_id = str(author_id)
        total_replies_today = 0
        author_replies_today = 0

        for post in self.data["posts"].values():
            action = post.get("actions", {}).get("reply", {})
            status = action.get("status")

            if status not in self.CONSUMING_STATUSES:
                continue

            timestamp = (
                action.get("completed_at")
                or action.get("last_attempt_at")
            )

            if self._local_date(timestamp) != today:
                continue

            total_replies_today += 1

            if str(post.get("author_id") or "") == author_id:
                author_replies_today += 1

        return {
            "total_replies": total_replies_today,
            "total_replies_today": total_replies_today,
            "author_replies_today": author_replies_today,
            "today": today.isoformat(),
        }

    def can_reply_to_author_today(
        self,
        author_id: str,
        max_total_replies: int | None = None,
    ) -> tuple[bool, str]:
        """
        Check both the global daily Reply limit and the per-author limit.

        max_total_replies is retained for compatibility with older callers.
        Both configured values in x_actions.json are authoritative.
        """
        if not author_id:
            return False, "missing_author_id"

        usage = self.get_reply_usage(author_id)

        configured_total_limit = self.get_action_limit("reply")
        configured_author_limit = self.get_reply_author_limit()

        if usage["total_replies_today"] >= configured_total_limit:
            return False, "daily_reply_limit_reached"

        if usage["author_replies_today"] >= configured_author_limit:
            return False, "author_daily_limit_reached"

        return True, "allowed"
