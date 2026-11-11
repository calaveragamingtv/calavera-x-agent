
import json
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone

from src.ai.gemini_analyzer import GeminiAnalyzer
from src.ai.gemini_reply_generator import GeminiReplyGenerator
from src.ai.gemini_like_evaluator import GeminiLikeEvaluator
from src.post_tracker import PostTracker


USERNAME = "monitored_accounts"

DATA_DIR = Path("data")
POSTS_FILE = DATA_DIR / "posts.json"
RETTIWT_FILE = DATA_DIR / "rettiwt_posts.json"
RANKING_FILE = DATA_DIR / "ranking.json"
LIKE_EVALUATIONS_FILE = DATA_DIR / "like_evaluations.json"

REPLY_ACTION_SCRIPT = Path("src/actions/x_reply.mjs")
REPOST_ACTION_SCRIPT = Path("src/actions/x_repost.mjs")
LIKE_ACTION_SCRIPT = Path("src/actions/x_like.mjs")
ACTION_CONFIG_FILE = Path("config/x_actions.json")

ACTION_SETTINGS = None


def load_action_settings():
    """Load and validate the centralized X actions configuration."""
    if not ACTION_CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Action config not found: {ACTION_CONFIG_FILE}"
        )

    with ACTION_CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError("Invalid action config: expected an object")

    actions = config.get("actions")
    limits = config.get("limits")
    scraping = config.get("scraping")

    if not isinstance(actions, dict):
        raise ValueError("Missing or invalid 'actions' configuration")
    if not isinstance(limits, dict):
        raise ValueError("Missing or invalid 'limits' configuration")
    if not isinstance(scraping, dict):
        raise ValueError("Missing or invalid 'scraping' configuration")

    for action in ("like", "reply", "repost"):
        action_config = actions.get(action)
        limit_config = limits.get(action)

        if not isinstance(action_config, dict) or not isinstance(
            action_config.get("enabled"), bool
        ):
            raise ValueError(f"Invalid actions.{action}.enabled setting")

        if not isinstance(limit_config, dict):
            raise ValueError(f"Missing limits.{action} configuration")

        max_per_day = limit_config.get("max_per_day")

        if (
            not isinstance(max_per_day, int)
            or isinstance(max_per_day, bool)
            or max_per_day < 0
        ):
            raise ValueError(
                f"Invalid limits.{action}.max_per_day setting"
            )

    reply_author_limit = limits["reply"].get(
        "max_per_author_per_day"
    )

    if (
        not isinstance(reply_author_limit, int)
        or isinstance(reply_author_limit, bool)
        or reply_author_limit < 0
    ):
        raise ValueError(
            "Invalid limits.reply.max_per_author_per_day setting"
        )

    min_like_score = limits["like"].get("min_score")

    if (
        not isinstance(min_like_score, int)
        or isinstance(min_like_score, bool)
        or not 0 <= min_like_score <= 100
    ):
        raise ValueError("Invalid limits.like.min_score setting")

    posts_per_account = scraping.get("posts_per_account")

    if (
        not isinstance(posts_per_account, int)
        or isinstance(posts_per_account, bool)
        or posts_per_account < 1
    ):
        raise ValueError(
            "Invalid scraping.posts_per_account setting"
        )

    return config


def get_action_limit(action, key="max_per_day"):
    """Read a limit from the configuration loaded for this execution."""
    global ACTION_SETTINGS

    if ACTION_SETTINGS is None:
        ACTION_SETTINGS = load_action_settings()

    try:
        return ACTION_SETTINGS["limits"][action][key]
    except KeyError as error:
        raise ValueError(
            f"Missing configuration value: limits.{action}.{key}"
        ) from error


def save_json(file_path, data):
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_rettiwt_posts():
    if not RETTIWT_FILE.exists():
        raise FileNotFoundError(
            f"Rettiwt output not found: {RETTIWT_FILE}"
        )

    with RETTIWT_FILE.open("r", encoding="utf-8") as file:
        posts = json.load(file)

    if not isinstance(posts, list):
        raise ValueError("Invalid Rettiwt output: expected a list")

    if not posts:
        raise RuntimeError("Rettiwt returned no posts")

    for post in posts:
        if not isinstance(post, dict) or not post.get("id"):
            raise ValueError("Invalid post: missing ID")

    return posts


def recover_interrupted_replies(tracker):
    print("\nRecovering interrupted replies...")

    recovered = tracker.recover_interrupted_actions("reply")

    print(f"Interrupted replies recovered: {recovered}")

    return recovered


def migrate_existing_reply_drafts(tracker):
    print("\nMigrating existing reply drafts...")

    migrated = tracker.migrate_legacy_reply_drafts()

    print(f"Existing reply drafts migrated: {migrated}")

    return migrated


def evaluate_reply_eligibility(tracker, rankings):
    print("\nEvaluating reply eligibility...")

    candidates = [
        item
        for item in rankings
        if item.get("recommended_action") == "reply"
    ]

    candidates.sort(
        key=lambda item: item.get("score", 0),
        reverse=True
    )

    daily_usage = tracker.get_action_usage("reply")
    remaining_today = max(
        0,
        get_action_limit("reply") - daily_usage["total_today"]
    )

    planned_total = 0
    planned_by_author = {}

    eligible = []
    blocked = []

    for item in candidates:
        post_id = str(item["post_id"])
        post = tracker.get_post(post_id)

        if not post:
            blocked.append((post_id, "post_not_found"))
            continue

        author_id = post.get("author_id")

        if not author_id:
            blocked.append((post_id, "missing_author_id"))
            continue

        author_id = str(author_id)
        usage = tracker.get_reply_usage(author_id)

        if planned_total >= remaining_today:
            blocked.append((post_id, "daily_reply_limit_reached"))
            continue

        if (
            usage["author_replies_today"]
            + planned_by_author.get(author_id, 0)
            >= get_action_limit(
                "reply",
                "max_per_author_per_day"
            )
        ):
            blocked.append((post_id, "author_daily_limit_reached"))
            continue

        eligible.append(post_id)

        planned_total += 1
        planned_by_author[author_id] = (
            planned_by_author.get(author_id, 0) + 1
        )

    print(f"Reply candidates from Gemini: {len(candidates)}")
    print(f"Eligible replies: {len(eligible)}")
    print(f"Blocked replies: {len(blocked)}")

    for post_id in eligible:
        print(f"Reply eligible: {post_id}")

    for post_id, reason in blocked:
        print(f"Reply blocked: {post_id} ({reason})")

    return eligible, blocked


def generate_reply_drafts(tracker, eligible_ids, rankings):
    print("\nGenerating reply drafts...")

    if not eligible_ids:
        print("No eligible posts. Skipping reply generation.")
        return

    ranking_by_id = {
        str(item["post_id"]): item
        for item in rankings
    }

    generator = GeminiReplyGenerator()

    for post_id in eligible_ids:
        post = tracker.get_post(post_id)
        ranking_item = ranking_by_id.get(post_id)

        if not post:
            print(
                f"Reply generation skipped: post not found ({post_id})"
            )
            continue

        reply_action = post["actions"]["reply"]

        if reply_action["status"] == "drafted":
            print(f"Existing reply draft preserved: {post_id}")
            continue

        if reply_action["status"] != "not_started":
            print(
                f"Reply generation skipped for {post_id}: "
                f"status is {reply_action['status']}"
            )
            continue

        try:
            reply_text = generator.generate_reply(
                post,
                ranking_item
            )

            if not reply_text:
                print(
                    f"Reply generation returned empty text: {post_id}"
                )
                continue

            payload = {
                "post_id": post_id,
                "reply_text": reply_text,
                "generated_at": datetime.now(timezone.utc).isoformat()
            }

            tracker.mark_action_drafted(
                post_id,
                "reply",
                payload
            )

            print(f"\nReply draft saved for post {post_id}:")
            print(reply_text)
            print(f"Reply length: {len(reply_text)}/280 characters")
            print("Reply status: drafted (not published)")

        except Exception as error:
            print(f"Reply generation failed for {post_id}: {error}")
            raise

    print("\nReply drafts generated or preserved.")


def test_reply_drafts(tracker, eligible_ids):
    print("\nTesting reply drafts with Node.js...")

    if not eligible_ids:
        print("No eligible replies to test.")
        return

    if not REPLY_ACTION_SCRIPT.exists():
        raise FileNotFoundError(
            f"Reply action script not found: {REPLY_ACTION_SCRIPT}"
        )

    live_enabled = load_reply_action_config()

    if live_enabled:
        print("LIVE REPLY MODE ENABLED")
        print(
            f"Maximum replies per day: {get_action_limit('reply')}"
        )
    else:
        print("DRY RUN MODE: Reply is disabled.")

    tested = 0
    published = 0
    failed = 0
    unknown = 0

    for post_id in eligible_ids:
        post = tracker.get_post(post_id)

        if not post:
            print(f"Reply skipped: post not found ({post_id})")
            continue

        reply_action = post.get("actions", {}).get("reply", {})
        current_status = reply_action.get("status")

        if current_status != "drafted":
            print(
                f"Reply skipped for {post_id}: "
                f"status is {current_status}"
            )
            continue

        author_id = post.get("author_id")

        if live_enabled:
            if not author_id:
                print(
                    f"Reply blocked for {post_id}: missing author ID"
                )
                continue

            allowed, reason = tracker.can_reply_to_author_today(
                str(author_id)
            )

            if not allowed:
                print(f"Reply blocked for {post_id}: {reason}")
                continue

            if published >= get_action_limit("reply"):
                print(
                    "Maximum replies reached during this execution: "
                    f"{get_action_limit('reply')}"
                )
                break

        payload = reply_action.get("payload") or {}
        reply_text = payload.get("reply_text")

        if not isinstance(reply_text, str) or not reply_text.strip():
            print(f"Invalid reply draft for post {post_id}")
            continue

        request = {
            "post_id": str(post_id),
            "reply_text": reply_text
        }

        print(f"\nProcessing reply for post {post_id}...")

        if live_enabled:
            tracker.mark_action_started(
                post_id,
                "reply",
                {
                    "post_id": str(post_id),
                    "reply_text": reply_text,
                    "mode": "live",
                    "stage": "sending"
                }
            )

        try:
            result = subprocess.run(
                ["node", str(REPLY_ACTION_SCRIPT)],
                input=json.dumps(request, ensure_ascii=False),
                capture_output=True,
                text=True,
                check=False,
                timeout=120
            )

            if result.stderr:
                print(result.stderr.strip())

            try:
                action_result = json.loads(result.stdout.strip())
            except (json.JSONDecodeError, TypeError):
                raise RuntimeError(
                    "Node.js did not return valid structured JSON. "
                    f"Exit code: {result.returncode}. "
                    f"Output: {result.stdout.strip()[:1000]}"
                )

            if not isinstance(action_result, dict):
                raise RuntimeError(
                    "Node.js returned an invalid result structure"
                )

            result_status = action_result.get("status")

            if not live_enabled:
                expected_values = {
                    "status": "dry_run",
                    "success": True,
                    "dry_run": True,
                    "published": False,
                    "original_post_id": str(post_id),
                    "reply_text": reply_text,
                    "reply_length": len(reply_text)
                }

                for field, expected in expected_values.items():
                    if action_result.get(field) != expected:
                        raise RuntimeError(
                            f"Invalid dry-run result: {field} "
                            f"expected {expected!r}, "
                            f"got {action_result.get(field)!r}"
                        )

                updated_post = tracker.get_post(post_id)
                updated_status = (
                    updated_post.get("actions", {})
                    .get("reply", {})
                    .get("status")
                )

                if updated_status != "drafted":
                    raise RuntimeError(
                        "Dry-run unexpectedly modified tracker status: "
                        f"{updated_status}"
                    )

                print(
                    f"DRY RUN CONFIRMED: {post_id}. "
                    "No publication was attempted."
                )

                tested += 1
                continue

            if action_result.get("original_post_id") != str(post_id):
                raise RuntimeError(
                    "Node.js returned a mismatched original_post_id"
                )

            if result_status == "completed":
                reply_id = action_result.get("reply_id")

                if (
                    action_result.get("success") is not True
                    or action_result.get("dry_run") is not False
                    or action_result.get("published") is not True
                    or not isinstance(reply_id, str)
                    or not reply_id.strip()
                ):
                    raise RuntimeError(
                        "Incomplete publication confirmation from Node.js"
                    )

                tracker.mark_action_result(
                    post_id,
                    "reply",
                    "completed",
                    external_id=reply_id
                )

                published += 1
                tested += 1

                print(
                    f"REPLY CONFIRMED: original_post={post_id}, "
                    f"reply_id={reply_id}, status=completed"
                )

            elif result_status == "failed":
                error_message = str(
                    action_result.get("error")
                    or "Publication failed before reaching X"
                )

                tracker.mark_action_result(
                    post_id,
                    "reply",
                    "failed",
                    error=error_message
                )

                failed += 1

                print(
                    f"REPLY FAILED: post={post_id}. "
                    f"Reason: {error_message}"
                )

            elif result_status == "unknown":
                error_message = str(
                    action_result.get("error")
                    or "Publication result could not be determined"
                )

                tracker.mark_action_result(
                    post_id,
                    "reply",
                    "unknown",
                    error=error_message
                )

                unknown += 1

                print(
                    f"REPLY UNKNOWN: post={post_id}. "
                    "Manual verification required. No automatic retry."
                )

            else:
                raise RuntimeError(
                    f"Unrecognized Node.js result status: {result_status!r}"
                )

        except Exception as error:
            if live_enabled:
                updated_post = tracker.get_post(post_id)
                current_status = (
                    updated_post.get("actions", {})
                    .get("reply", {})
                    .get("status")
                ) if updated_post else None

                if current_status == "processing":
                    tracker.mark_action_result(
                        post_id,
                        "reply",
                        "unknown",
                        error=(
                            "Reply execution result is uncertain: "
                            f"{error}. Manual verification required."
                        )
                    )

                    unknown += 1

                    print(
                        f"REPLY UNKNOWN: post={post_id}. "
                        "Tracker locked against automatic retry."
                    )
                else:
                    print(
                        f"Reply processing error for {post_id}: {error}"
                    )

            else:
                print(
                    f"Dry-run execution failed for {post_id}: {error}"
                )

            continue

    print("\nReply execution summary:")
    print(f"Results processed: {tested}")
    print(f"Replies confirmed: {published}")
    print(f"Confirmed failures: {failed}")
    print(f"Uncertain results: {unknown}")

    if not live_enabled:
        print("DRY RUN: no replies were published to X.")


def generate_repost_drafts(tracker, rankings):
    print("\nGenerating repost drafts...")

    candidates = [
        item
        for item in rankings
        if item.get("recommended_action") == "repost"
    ]

    candidates.sort(
        key=lambda item: item.get("score", 0),
        reverse=True
    )

    generated = 0

    for item in candidates:
        post_id = str(item.get("post_id", "")).strip()

        if not post_id:
            continue

        post = tracker.get_post(post_id)

        if not post:
            print(f"Repost skipped: post not found ({post_id})")
            continue

        repost_action = post["actions"]["repost"]
        status = repost_action["status"]

        if status == "drafted":
            print(f"Existing repost draft preserved: {post_id}")
            continue

        if status not in ("not_started", "failed"):
            print(f"Repost skipped for {post_id}: status is {status}")
            continue

        payload = {
            "post_id": post_id,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

        tracker.mark_action_drafted(
            post_id,
            "repost",
            payload
        )

        print(f"Repost draft saved: {post_id}")
        generated += 1

    print(f"Repost drafts generated or preserved: {generated}")


def test_repost_drafts(tracker, rankings):
    print("\nTesting repost drafts with Node.js...")

    if not REPOST_ACTION_SCRIPT.exists():
        raise FileNotFoundError(
            f"Repost action script not found: {REPOST_ACTION_SCRIPT}"
        )

    live_enabled = load_repost_action_config()

    if live_enabled:
        print("LIVE REPOST MODE ENABLED")
        print(
            f"Maximum reposts per day: {get_action_limit('repost')}"
        )
    else:
        print("DRY RUN MODE: Repost is disabled.")

    if live_enabled:
        usage = tracker.get_action_usage("repost")
        remaining_reposts = max(
            0,
            get_action_limit("repost") - usage["total_today"]
        )
    else:
        remaining_reposts = get_action_limit("repost")

    if remaining_reposts <= 0:
        print(
            f"Daily repost limit already reached: "
            f"{get_action_limit('repost')}"
        )
        return

    candidates = []

    for item in rankings:
        if item.get("recommended_action") != "repost":
            continue

        post_id = str(item.get("post_id", "")).strip()

        if not post_id:
            continue

        post = tracker.get_post(post_id)

        if not post:
            continue

        repost_action = post.get("actions", {}).get("repost", {})

        if repost_action.get("status") != "drafted":
            continue

        candidates.append(post_id)

        if len(candidates) >= remaining_reposts:
            break

    print(f"Pending repost drafts selected: {len(candidates)}")

    if not candidates:
        print("No pending repost drafts to test.")
        return

    tested = 0
    published = 0
    failed = 0
    unknown = 0

    for post_id in candidates:
        post = tracker.get_post(post_id)

        if not post:
            print(f"Repost skipped: post not found ({post_id})")
            continue

        current_status = (
            post.get("actions", {})
            .get("repost", {})
            .get("status")
        )

        if current_status != "drafted":
            print(
                f"Repost skipped for {post_id}: "
                f"status is {current_status}"
            )
            continue

        request = {"post_id": str(post_id)}

        print(f"\nProcessing repost for post {post_id}...")

        if live_enabled:
            allowed, reason = tracker.can_perform_action_today(
                "repost",
                get_action_limit("repost")
            )

            if not allowed:
                print(f"Repost blocked for {post_id}: {reason}")
                break

            tracker.mark_action_started(
                post_id,
                "repost",
                {
                    "post_id": str(post_id),
                    "mode": "live",
                    "stage": "sending"
                }
            )

        try:
            result = subprocess.run(
                ["node", str(REPOST_ACTION_SCRIPT)],
                input=json.dumps(request, ensure_ascii=False),
                capture_output=True,
                text=True,
                check=False,
                timeout=120
            )

            if result.stderr:
                print(result.stderr.strip())

            try:
                action_result = json.loads(result.stdout.strip())
            except (json.JSONDecodeError, TypeError):
                raise RuntimeError(
                    "Node.js did not return valid structured JSON. "
                    f"Exit code: {result.returncode}. "
                    f"Output: {result.stdout.strip()[:1000]}"
                )

            if not isinstance(action_result, dict):
                raise RuntimeError(
                    "Node.js returned an invalid result structure"
                )

            if action_result.get("original_post_id") != str(post_id):
                raise RuntimeError(
                    "Node.js returned a mismatched original_post_id"
                )

            result_status = action_result.get("status")

            if not live_enabled:
                expected = {
                    "status": "dry_run",
                    "success": True,
                    "dry_run": True,
                    "reposted": False,
                    "original_post_id": str(post_id)
                }

                for field, value in expected.items():
                    if action_result.get(field) != value:
                        raise RuntimeError(
                            f"Invalid repost dry-run result: {field} "
                            f"expected {value!r}, "
                            f"got {action_result.get(field)!r}"
                        )

                updated_post = tracker.get_post(post_id)
                updated_status = (
                    updated_post.get("actions", {})
                    .get("repost", {})
                    .get("status")
                ) if updated_post else None

                if updated_status != "drafted":
                    raise RuntimeError(
                        "Dry-run unexpectedly modified tracker status: "
                        f"{updated_status}"
                    )

                print(
                    f"DRY RUN CONFIRMED: {post_id}. "
                    "No repost was sent to X."
                )

                tested += 1
                continue

            if result_status == "completed":
                if (
                    action_result.get("success") is not True
                    or action_result.get("dry_run") is not False
                    or action_result.get("reposted") is not True
                ):
                    raise RuntimeError(
                        "Incomplete repost confirmation from Node.js"
                    )

                tracker.mark_action_result(
                    post_id,
                    "repost",
                    "completed",
                    external_id=str(post_id)
                )

                published += 1
                tested += 1

                print(
                    f"REPOST CONFIRMED: post={post_id}, "
                    "status=completed"
                )

            elif result_status == "failed":
                error_message = str(
                    action_result.get("error")
                    or "Repost failed before reaching X"
                )

                tracker.mark_action_result(
                    post_id,
                    "repost",
                    "failed",
                    error=error_message
                )

                failed += 1

                print(
                    f"REPOST FAILED: post={post_id}. "
                    f"Reason: {error_message}"
                )

            elif result_status == "unknown":
                error_message = str(
                    action_result.get("error")
                    or "Repost result could not be determined"
                )

                tracker.mark_action_result(
                    post_id,
                    "repost",
                    "unknown",
                    error=error_message
                )

                unknown += 1

                print(
                    f"REPOST UNKNOWN: post={post_id}. "
                    "Manual verification required. No automatic retry."
                )

            else:
                raise RuntimeError(
                    f"Unrecognized Node.js result status: {result_status!r}"
                )

        except Exception as error:
            if live_enabled:
                updated_post = tracker.get_post(post_id)
                current_status = (
                    updated_post.get("actions", {})
                    .get("repost", {})
                    .get("status")
                ) if updated_post else None

                if current_status == "processing":
                    tracker.mark_action_result(
                        post_id,
                        "repost",
                        "unknown",
                        error=(
                            "Repost execution result is uncertain: "
                            f"{error}. Manual verification required."
                        )
                    )

                    unknown += 1

                    print(
                        f"REPOST UNKNOWN: post={post_id}. "
                        "Tracker locked against automatic retry."
                    )
                else:
                    print(
                        f"Repost processing error for {post_id}: {error}"
                    )

            else:
                print(
                    f"Repost dry-run failed for {post_id}: {error}"
                )

            continue

    print("\nRepost execution summary:")
    print(f"Results processed: {tested}")
    print(f"Reposts confirmed: {published}")
    print(f"Confirmed failures: {failed}")
    print(f"Uncertain results: {unknown}")

    if not live_enabled:
        print("DRY RUN: no reposts were published to X.")


def load_like_evaluations():
    if not LIKE_EVALUATIONS_FILE.exists():
        return {}

    with LIKE_EVALUATIONS_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError("Invalid like evaluations file")

    evaluations = data.get("evaluations")

    if not isinstance(evaluations, dict):
        raise ValueError("Invalid like evaluations structure")

    return {
        str(post_id): evaluation
        for post_id, evaluation in evaluations.items()
        if isinstance(evaluation, dict)
    }


def evaluate_like_candidates(tracker, active_post_ids):
    print("\nEvaluating current posts for Likes with Gemini...")

    evaluations = load_like_evaluations()
    pending_posts = []

    for post_id in active_post_ids:
        post = tracker.get_post(post_id)

        if not post:
            continue

        like_action = post.get("actions", {}).get("like", {})
        status = like_action.get("status", "not_started")

        if status in ("completed", "unknown", "processing"):
            continue

        if status not in ("not_started", "failed", "drafted"):
            continue

        if str(post_id) in evaluations:
            continue

        pending_posts.append({
            "id": str(post_id),
            "text": post.get("text", ""),
            "url": post.get("url"),
            "expanded_urls": post.get("expanded_urls", []),
            "created_at": post.get("created_at"),
            "author_id": post.get("author_id"),
            "author": post.get("author"),
            "monitored_by": post.get("monitored_by", []),
            "content_type": post.get("content_type"),
            "likes": post.get("likes", 0),
            "views": post.get("views", 0),
            "replies": post.get("replies", 0),
            "reposts": post.get("reposts", 0)
        })

    print(f"Posts pending Like evaluation: {len(pending_posts)}")

    if not pending_posts:
        print("No posts require Like evaluation. Skipping Gemini.")
        return evaluations

    evaluator = GeminiLikeEvaluator()
    result = evaluator.evaluate_posts(pending_posts)
    new_evaluations = result["evaluations"]

    for item in new_evaluations:
        post_id = str(item["post_id"])
        evaluations[post_id] = item
        decision = "LIKE" if item["should_like"] else "SKIP"

        print(
            f"Like evaluation: {post_id} | {decision} | "
            f"score={item['score']} | category={item['category']}"
        )

    save_json(LIKE_EVALUATIONS_FILE, {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(evaluations),
        "evaluations": evaluations
    })

    print(f"New Like evaluations saved: {len(new_evaluations)}")
    print(f"Total cached Like evaluations: {len(evaluations)}")

    return evaluations


def has_platform_link(post, platforms=None):
    if not isinstance(post, dict):
        return False

    if platforms is None:
        platforms = (
            r"twitch\.tv",
            r"kick\.com",
            r"youtube\.com",
            r"youtu\.be"
        )

    searchable_parts = [post.get("text", ""), post.get("url", "")]
    expanded_urls = post.get("expanded_urls", [])

    if isinstance(expanded_urls, list):
        searchable_parts.extend(
            url for url in expanded_urls if isinstance(url, str)
        )

    searchable_content = " ".join(
        part for part in searchable_parts if isinstance(part, str)
    )
    domains = "|".join(platforms)
    pattern = (
        r"(?:https?://)?(?:www\.)?(?:"
        + domains
        + r")(?:[/:?#]|$)"
    )

    return bool(re.search(pattern, searchable_content, re.IGNORECASE))


def is_stream_announcement(post):
    if not isinstance(post, dict):
        return False

    monitored_by = post.get("monitored_by", [])

    if not isinstance(monitored_by, list):
        return False

    is_streamer = any(
        isinstance(account, dict)
        and str(account.get("account_type", "")).lower() == "streamer"
        for account in monitored_by
    )

    if not is_streamer:
        return False

    return has_platform_link(
        post,
        platforms=(r"twitch\.tv", r"kick\.com")
    )


def generate_like_drafts(tracker, evaluations, active_post_ids):
    print("\nGenerating like drafts...")

    active_post_id_set = {str(post_id) for post_id in active_post_ids}

    evaluations = {
        str(post_id): evaluation
        for post_id, evaluation in evaluations.items()
    }

    outdated_drafts = 0

    for post_id in tracker.get_drafted_action_ids("like"):
        post_id = str(post_id)

        if post_id not in active_post_id_set:
            continue

        if post_id not in evaluations:
            continue

        evaluation = evaluations[post_id]

        try:
            score = int(evaluation.get("score", 0))
        except (TypeError, ValueError):
            score = 0

        if (
            evaluation.get("should_like") is not True
            or score < get_action_limit("like", "min_score")
        ):
            if tracker.cancel_like_draft(
                post_id,
                reason="No longer approved by current evaluation"
            ):
                outdated_drafts += 1

    print(f"Outdated Like drafts canceled: {outdated_drafts}")

    candidates = []

    for post_id in active_post_ids:
        post_id = str(post_id)
        evaluation = evaluations.get(post_id)

        if not isinstance(evaluation, dict):
            continue

        if evaluation.get("should_like") is not True:
            continue

        try:
            score = int(evaluation.get("score", 0))
        except (TypeError, ValueError):
            continue

        if score < get_action_limit("like", "min_score"):
            continue

        post = tracker.get_post(post_id)

        if not post:
            continue

        like_action = post.get("actions", {}).get("like", {})
        status = like_action.get("status", "not_started")

        if status in ("completed", "unknown", "processing"):
            continue

        if status not in ("not_started", "failed", "drafted"):
            continue

        platform_link = has_platform_link(post)
        stream_announcement = is_stream_announcement(post)

        if platform_link:
            priority = 2 if stream_announcement else 1
            reason = (
                "stream_announcement"
                if stream_announcement
                else "platform_link"
            )
            score = max(score, 90)
        else:
            priority = 0
            reason = "interesting_post"

        author_id = str(
            post.get("author_id") or f"unknown_{post_id}"
        )

        candidates.append({
            "post_id": post_id,
            "author_id": author_id,
            "reason": reason,
            "score": score,
            "category": evaluation.get("category", "other"),
            "evaluation_reason": evaluation.get("reason", ""),
            "priority": priority,
            "relationship": platform_link
        })

    candidates.sort(
        key=lambda item: (
            item["priority"],
            item["score"]
        ),
        reverse=True
    )

    unique_authors = {
        candidate["author_id"]
        for candidate in candidates
    }

    print(
        f"Unique authors among Like candidates: "
        f"{len(unique_authors)}"
    )

    for candidate in candidates:
        print(
            f"Like candidate author: "
            f"post={candidate['post_id']} "
            f"author={candidate['author_id']} "
            f"relationship={candidate['relationship']} "
            f"score={candidate['score']}"
        )

    selected = []
    selected_authors = set()

    relationship_selected = False
    genuine_selected = False

    for candidate in candidates:
        if len(selected) >= get_action_limit("like"):
            break

        author_id = candidate["author_id"]

        if author_id in selected_authors:
            continue

        if candidate["relationship"]:
            if relationship_selected:
                continue
            relationship_selected = True
        else:
            if genuine_selected:
                continue
            genuine_selected = True

        selected.append(candidate)
        selected_authors.add(author_id)

    if len(selected) < get_action_limit("like"):
        for candidate in candidates:
            if len(selected) >= get_action_limit("like"):
                break

            if candidate in selected:
                continue

            author_id = candidate["author_id"]

            if author_id in selected_authors:
                continue

            selected.append(candidate)
            selected_authors.add(author_id)

    print(f"Like candidates approved by Gemini: {len(candidates)}")
    print(
        f"Relationship candidates: "
        f"{sum(1 for item in candidates if item['relationship'])}"
    )
    print(
        f"Genuine content candidates: "
        f"{sum(1 for item in candidates if not item['relationship'])}"
    )
    print(f"Selected this execution: {len(selected)}")

    for candidate in selected:
        post_id = candidate["post_id"]
        post = tracker.get_post(post_id)
        like_action = post["actions"]["like"]

        if like_action["status"] == "drafted":
            print(
                f"Existing like draft preserved: {post_id} "
                f"(reason={candidate['reason']})"
            )
            continue

        payload = {
            "post_id": post_id,
            "reason": candidate["reason"],
            "score": candidate["score"],
            "category": candidate["category"],
            "evaluation_reason": candidate["evaluation_reason"],
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

        tracker.mark_action_drafted(
            post_id,
            "like",
            payload
        )

        print(
            f"Like draft saved: {post_id} "
            f"(reason={candidate['reason']}, "
            f"score={candidate['score']})"
        )

    return [
        candidate["post_id"]
        for candidate in selected
    ]


def load_action_enabled(action):
    """Read the enabled flag from the centralized actions configuration."""
    global ACTION_SETTINGS

    if ACTION_SETTINGS is None:
        ACTION_SETTINGS = load_action_settings()

    try:
        return ACTION_SETTINGS["actions"][action]["enabled"]
    except KeyError as error:
        raise ValueError(
            f"Missing configuration value: actions.{action}.enabled"
        ) from error


def load_like_action_config():
    return load_action_enabled("like")


def load_reply_action_config():
    return load_action_enabled("reply")


def load_repost_action_config():
    return load_action_enabled("repost")


def test_like_drafts(tracker, selected_ids):
    print("\nTesting like drafts with Node.js...")

    live_enabled = load_like_action_config()

    if live_enabled:
        print("LIVE LIKE MODE ENABLED")
        print(f"Maximum likes per day: {get_action_limit('like')}")
    else:
        print("DRY RUN MODE: Like is disabled.")

    if not selected_ids:
        print("No eligible likes to test.")
        return

    if not LIKE_ACTION_SCRIPT.exists():
        raise FileNotFoundError(
            f"Like action script not found: {LIKE_ACTION_SCRIPT}"
        )

    tested = 0
    liked = 0
    failed = 0
    unknown = 0

    for post_id in selected_ids:
        post = tracker.get_post(post_id)

        if not post:
            print(f"Like skipped: post not found ({post_id})")
            continue

        like_action = post.get("actions", {}).get("like", {})
        current_status = like_action.get("status")

        if current_status != "drafted":
            print(
                f"Like skipped for {post_id}: "
                f"status is {current_status}"
            )
            continue

        request = {"post_id": str(post_id)}

        print(f"\nProcessing like for post {post_id}...")

        if live_enabled:
            allowed, reason = tracker.can_perform_action_today(
                "like",
                get_action_limit("like")
            )

            if not allowed:
                print(f"Like blocked for {post_id}: {reason}")
                break

            tracker.mark_action_started(
                post_id,
                "like",
                {
                    "post_id": str(post_id),
                    "mode": "live",
                    "stage": "sending"
                }
            )

        try:
            result = subprocess.run(
                ["node", str(LIKE_ACTION_SCRIPT)],
                input=json.dumps(request, ensure_ascii=False),
                capture_output=True,
                text=True,
                check=False,
                timeout=120
            )

            if result.stderr:
                print(result.stderr.strip())

            try:
                action_result = json.loads(result.stdout.strip())
            except (json.JSONDecodeError, TypeError):
                raise RuntimeError(
                    "Node.js did not return valid structured JSON. "
                    f"Exit code: {result.returncode}. "
                    f"Output: {result.stdout.strip()[:1000]}"
                )

            if not isinstance(action_result, dict):
                raise RuntimeError(
                    "Node.js returned an invalid result structure"
                )

            if action_result.get("original_post_id") != str(post_id):
                raise RuntimeError(
                    "Node.js returned a mismatched original_post_id"
                )

            result_status = action_result.get("status")

            if not live_enabled:
                expected = {
                    "status": "dry_run",
                    "success": True,
                    "dry_run": True,
                    "liked": False,
                    "original_post_id": str(post_id)
                }

                for field, value in expected.items():
                    if action_result.get(field) != value:
                        raise RuntimeError(
                            f"Invalid Like dry-run result: {field} "
                            f"expected {value!r}, "
                            f"got {action_result.get(field)!r}"
                        )

                updated_post = tracker.get_post(post_id)
                updated_status = (
                    updated_post.get("actions", {})
                    .get("like", {})
                    .get("status")
                ) if updated_post else None

                if updated_status != "drafted":
                    raise RuntimeError(
                        "Dry-run unexpectedly modified tracker status: "
                        f"{updated_status}"
                    )

                print(
                    f"DRY RUN CONFIRMED: {post_id}. "
                    "No like was sent to X."
                )

                tested += 1
                continue

            if result_status == "completed":
                if (
                    action_result.get("success") is not True
                    or action_result.get("dry_run") is not False
                    or action_result.get("liked") is not True
                ):
                    raise RuntimeError(
                        "Incomplete Like confirmation from Node.js"
                    )

                tracker.mark_action_result(
                    post_id,
                    "like",
                    "completed",
                    external_id=str(post_id)
                )

                liked += 1
                tested += 1

                print(
                    f"LIKE CONFIRMED: post={post_id}, "
                    "status=completed"
                )

            elif result_status == "failed":
                error_message = str(
                    action_result.get("error")
                    or "Like failed before reaching X"
                )

                tracker.mark_action_result(
                    post_id,
                    "like",
                    "failed",
                    error=error_message
                )

                failed += 1

                print(
                    f"LIKE FAILED: post={post_id}. "
                    f"Reason: {error_message}"
                )

            elif result_status == "unknown":
                error_message = str(
                    action_result.get("error")
                    or "Like result could not be determined"
                )

                tracker.mark_action_result(
                    post_id,
                    "like",
                    "unknown",
                    error=error_message
                )

                unknown += 1

                print(
                    f"LIKE UNKNOWN: post={post_id}. "
                    "Manual verification required. No automatic retry."
                )

            else:
                raise RuntimeError(
                    f"Unrecognized Node.js result status: {result_status!r}"
                )

        except Exception as error:
            if live_enabled:
                updated_post = tracker.get_post(post_id)
                current_status = (
                    updated_post.get("actions", {})
                    .get("like", {})
                    .get("status")
                ) if updated_post else None

                if current_status == "processing":
                    tracker.mark_action_result(
                        post_id,
                        "like",
                        "unknown",
                        error=(
                            "Like execution result is uncertain: "
                            f"{error}. Manual verification required."
                        )
                    )

                    unknown += 1

                    print(
                        f"LIKE UNKNOWN: post={post_id}. "
                        "Tracker locked against automatic retry."
                    )
                else:
                    print(
                        f"Like processing error for {post_id}: {error}"
                    )

            else:
                print(
                    f"Like dry-run failed for {post_id}: {error}"
                )

            continue

    print("\nLike execution summary:")
    print(f"Results processed: {tested}")
    print(f"Likes confirmed: {liked}")
    print(f"Confirmed failures: {failed}")
    print(f"Uncertain results: {unknown}")

    if not live_enabled:
        print("DRY RUN: no likes were sent to X.")


def main():
    global ACTION_SETTINGS

    print("Starting X Agent")

    ACTION_SETTINGS = load_action_settings()
    settings = ACTION_SETTINGS

    print(
        "Configured daily limits: "
        f"Likes={settings['limits']['like']['max_per_day']}, "
        f"Replies={settings['limits']['reply']['max_per_day']}, "
        f"Reposts={settings['limits']['repost']['max_per_day']}"
    )
    print(
        "Configured reply limit per author per day: "
        f"{settings['limits']['reply']['max_per_author_per_day']}"
    )
    print(
        "Configured minimum Like score: "
        f"{settings['limits']['like']['min_score']}"
    )
    print(
        "Configured posts per monitored account: "
        f"{settings['scraping']['posts_per_account']}"
    )

    print(
        "Action modes: "
        f"Like={'LIVE' if settings['actions']['like']['enabled'] else 'DRY RUN'}, "
        f"Reply={'LIVE' if settings['actions']['reply']['enabled'] else 'DRY RUN'}, "
        f"Repost={'LIVE' if settings['actions']['repost']['enabled'] else 'DRY RUN'}"
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    posts = load_rettiwt_posts()

    print(f"Loaded {len(posts)} posts from Rettiwt")

    active_post_ids = list(dict.fromkeys(
        str(post["id"])
        for post in posts
    ))
    active_post_id_set = set(active_post_ids)

    print(
        f"Active posts in current per-account windows: "
        f"{len(active_post_ids)}"
    )

    save_json(POSTS_FILE, {
        "account": USERNAME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(posts),
        "posts": posts
    })

    tracker = PostTracker()

    migrate_existing_reply_drafts(tracker)

    recover_interrupted_replies(tracker)

    print("\nRecovering interrupted likes...")
    recovered_likes = tracker.recover_interrupted_actions("like")
    print(f"Interrupted likes recovered: {recovered_likes}")

    print("\nRecovering interrupted reposts...")
    recovered_reposts = tracker.recover_interrupted_actions("repost")
    print(f"Interrupted reposts recovered: {recovered_reposts}")

    new_ids = tracker.register_posts(posts)

    print(f"New posts registered: {len(new_ids)}")

    pending_ids = []

    for post_id in active_post_ids:
        tracked_post = tracker.get_post(post_id)

        if not tracked_post:
            continue

        analysis_status = tracked_post.get("analysis", {}).get("status")

        if analysis_status in ("pending", "failed"):
            pending_ids.append(post_id)

    print(f"Current posts pending analysis: {len(pending_ids)}")

    analyzer = None

    if pending_ids:
        pending_posts = []

        for post_id in pending_ids:
            tracked_post = tracker.get_post(post_id)

            pending_posts.append({
                "id": tracked_post["id"],
                "text": tracked_post["text"],
                "url": tracked_post.get("url"),
                "created_at": tracked_post.get("created_at")
            })

        analyzer = GeminiAnalyzer()

        try:
            result = analyzer.analyze_posts(pending_posts)

            ranking_by_id = {
                str(item["post_id"]): item
                for item in result["ranking"]
            }

            for post_id in pending_ids:
                tracker.mark_analysis_completed(
                    post_id,
                    ranking_by_id[post_id]
                )

            print(f"Successfully analyzed {len(pending_ids)} posts")

        except Exception as error:
            print(f"Gemini analysis failed: {error}")

            for post_id in pending_ids:
                tracker.mark_analysis_failed(
                    post_id,
                    str(error)
                )

            raise

    else:
        print("No posts require analysis. Skipping Gemini.")

    all_rankings = []

    for post_id in active_post_ids:
        post = tracker.get_post(post_id)

        if not post:
            continue

        analysis = post.get("analysis", {})

        if analysis.get("status") == "completed":
            ranking_item = analysis.get("result")

            if ranking_item:
                all_rankings.append(ranking_item)

    all_rankings.sort(
        key=lambda item: item.get("score", 0),
        reverse=True
    )

    save_json(RANKING_FILE, {
        "account": USERNAME,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "model": analyzer.model if analyzer else None,
        "total": len(all_rankings),
        "ranking": all_rankings
    })

    print(
        f"Ranking saved: {len(all_rankings)} analyzed posts "
        f"from {len(active_post_ids)} active posts"
    )

    eligible, blocked = evaluate_reply_eligibility(
        tracker,
        all_rankings
    )

    generate_reply_drafts(
        tracker,
        eligible,
        all_rankings
    )

    test_reply_drafts(
        tracker,
        eligible
    )

    generate_repost_drafts(
        tracker,
        all_rankings
    )

    test_repost_drafts(
        tracker,
        all_rankings
    )

    like_evaluations = evaluate_like_candidates(
        tracker,
        active_post_ids
    )

    like_ids = generate_like_drafts(
        tracker,
        like_evaluations,
        active_post_ids
    )

    test_like_drafts(
        tracker,
        like_ids
    )

    print(f"\nActive posts processed this run: {len(active_post_id_set)}")
    print(
        f"Historical posts retained in tracker: "
        f"{len(tracker.data['posts'])}"
    )
    print("\nX Agent completed successfully")


if __name__ == "__main__":
    main()
