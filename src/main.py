import json
import re
import subprocess
import traceback
from pathlib import Path
from datetime import datetime, timezone

from src.ai.gemini_analyzer import GeminiAnalyzer
from src.ai.gemini_reply_generator import GeminiReplyGenerator
from src.ai.gemini_like_evaluator import GeminiLikeEvaluator
from src.post_tracker import PostTracker


USERNAME = "monitored_accounts"
LIMIT = 10
MAX_TOTAL_REPLIES = 2

MAX_REPOSTS_PER_RUN = 2

MAX_LIKES_PER_RUN = 2
MIN_LIKE_SCORE = 75

DATA_DIR = Path("data")
POSTS_FILE = DATA_DIR / "posts.json"
RETTIWT_FILE = DATA_DIR / "rettiwt_posts.json"
RANKING_FILE = DATA_DIR / "ranking.json"
LIKE_EVALUATIONS_FILE = DATA_DIR / "like_evaluations.json"

REPLY_ACTION_SCRIPT = Path("src/actions/x_reply.mjs")
REPOST_ACTION_SCRIPT = Path("src/actions/x_repost.mjs")
LIKE_ACTION_SCRIPT = Path("src/actions/x_like.mjs")
ACTION_CONFIG_FILE = Path("config/x_actions.json")


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

    # The tracker counts attempts already made today (Argentina time).
    # Drafts selected in this pass are added to that daily usage.
    daily_usage = tracker.get_action_usage("reply")
    remaining_today = max(
        0,
        MAX_TOTAL_REPLIES - daily_usage["total_today"]
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
            >= 1
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
            print(f"Reply generation skipped: post not found ({post_id})")
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
                print(f"Reply generation returned empty text: {post_id}")
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
            traceback.print_exc()
            continue

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
            f"Maximum replies per day: {MAX_TOTAL_REPLIES}"
        )
    else:
        print("DRY RUN MODE: Reply is disabled.")

    tested = 0
    published = 0

    for post_id in eligible_ids:
        post = tracker.get_post(post_id)

        if not post:
            print(f"Reply test skipped: post not found ({post_id})")
            continue

        reply_action = post.get("actions", {}).get("reply", {})

        if reply_action.get("status") != "drafted":
            print(
                f"Reply test skipped for {post_id}: "
                f"status is {reply_action.get('status')}"
            )
            continue

        if live_enabled:
            author_id = post.get("author_id")
            allowed, reason = tracker.can_reply_to_author_today(
                str(author_id) if author_id else "",
                MAX_TOTAL_REPLIES
            )

            if not allowed:
                print(
                    f"Reply blocked for {post_id}: {reason}"
                )
                continue

            if published >= MAX_TOTAL_REPLIES:
                print(
                    f"Maximum replies per day reached: "
                    f"{MAX_TOTAL_REPLIES}"
                )
                break

        payload = reply_action.get("payload") or {}
        reply_text = payload.get("reply_text")

        if not isinstance(reply_text, str) or not reply_text.strip():
            raise RuntimeError(
                f"Invalid reply draft for post {post_id}"
            )

        request = {
            "post_id": str(post_id),
            "reply_text": reply_text
        }

        print(f"\nTesting reply for post {post_id}...")

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
                check=False
            )

            if result.stderr:
                print(result.stderr.strip())

            if result.returncode != 0:
                raise RuntimeError(
                    f"Node.js reply action failed for post {post_id}. "
                    f"Exit code: {result.returncode}. "
                    f"Output: {result.stdout.strip()}"
                )

            try:
                action_result = json.loads(result.stdout.strip())
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"Invalid JSON returned by Node.js for post {post_id}: "
                    f"{result.stdout.strip()}"
                ) from error

            if not isinstance(action_result, dict):
                raise RuntimeError(
                    f"Invalid Node.js response for post {post_id}"
                )

            if live_enabled:
                expected_values = {
                    "success": True,
                    "dry_run": False,
                    "published": True,
                    "original_post_id": str(post_id)
                }

                for field, expected in expected_values.items():
                    actual = action_result.get(field)

                    if actual != expected:
                        raise RuntimeError(
                            f"Unexpected Node.js result for post {post_id}: "
                            f"{field} expected {expected!r}, got {actual!r}"
                        )

                reply_id = action_result.get("reply_id")

                if not isinstance(reply_id, str) or not reply_id.strip():
                    raise RuntimeError(
                        f"Missing reply ID for post {post_id}"
                    )

                tracker.mark_action_result(
                    post_id,
                    "reply",
                    "completed",
                    external_id=reply_id
                )

                print(
                    f"LIVE REPLY CONFIRMED: post={post_id}, "
                    f"reply_id={reply_id}, tracker_status=completed"
                )

                published += 1

            else:
                expected_values = {
                    "success": True,
                    "dry_run": True,
                    "published": False,
                    "original_post_id": str(post_id),
                    "reply_text": reply_text,
                    "reply_length": len(reply_text)
                }

                for field, expected in expected_values.items():
                    actual = action_result.get(field)

                    if actual != expected:
                        raise RuntimeError(
                            f"Unexpected Node.js result for post {post_id}: "
                            f"{field} expected {expected!r}, got {actual!r}"
                        )

                updated_post = tracker.get_post(post_id)
                updated_status = (
                    updated_post.get("actions", {})
                    .get("reply", {})
                    .get("status")
                )

                if updated_status != "drafted":
                    raise RuntimeError(
                        f"Unexpected tracker state after dry run for "
                        f"{post_id}: {updated_status}"
                    )

                print(
                    "Validated: success=True, dry_run=True, "
                    "published=False, tracker_status=drafted"
                )

            tested += 1

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
                            "Reply execution result is uncertain. "
                            "Manual verification required."
                        )
                    )

                    print(
                        f"Reply result is uncertain for {post_id}. "
                        "Tracker status set to unknown. "
                        "Manual verification is required."
                    )

            raise

    print(f"\nReply drafts tested successfully: {tested}")

    if live_enabled:
        print(f"Live Replies confirmed: {published}")
        print(
            f"Maximum replies per day: {MAX_TOTAL_REPLIES}"
        )
    else:
        print("DRY RUN: no replies were published to X.")
        print("All tested replies remain in drafted state.")


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
            f"Maximum reposts per day: {MAX_REPOSTS_PER_RUN}"
        )
    else:
        print("DRY RUN MODE: Repost is disabled.")

    # The external scheduler runs this workflow once per day. Use the
    # tracker's Argentina-day usage as the source of truth so an accidental
    # second execution cannot exceed the daily limit.
    if live_enabled:
        repost_usage = tracker.get_action_usage("repost")
        remaining_reposts = max(
            0,
            MAX_REPOSTS_PER_RUN - repost_usage["total_today"]
        )
    else:
        remaining_reposts = MAX_REPOSTS_PER_RUN

    if remaining_reposts <= 0:
        print(
            f"Daily repost limit already reached: "
            f"{MAX_REPOSTS_PER_RUN}"
        )
        return

    # Preserve ranking order and select only pending drafts within
    # the remaining daily allowance.
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

    for post_id in candidates:
        request = {"post_id": post_id}

        print(f"\nTesting repost for post {post_id}...")

        if live_enabled:
            allowed, reason = tracker.can_perform_action_today(
                "repost",
                MAX_REPOSTS_PER_RUN
            )

            if not allowed:
                print(f"Repost blocked for {post_id}: {reason}")
                break

            tracker.mark_action_started(
                post_id,
                "repost",
                {
                    "post_id": post_id,
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
                check=False
            )

            if result.stderr:
                print(result.stderr.strip())

            if result.returncode != 0:
                raise RuntimeError(
                    f"Node.js repost action failed for post {post_id}. "
                    f"Exit code: {result.returncode}. "
                    f"Output: {result.stdout.strip()}"
                )

            try:
                action_result = json.loads(result.stdout.strip())
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"Invalid JSON returned by Node.js for repost "
                    f"{post_id}: {result.stdout.strip()}"
                ) from error

            if not isinstance(action_result, dict):
                raise RuntimeError(
                    f"Invalid Node.js response for repost {post_id}"
                )

            if live_enabled:
                expected = {
                    "success": True,
                    "dry_run": False,
                    "published": True,
                    "original_post_id": post_id
                }

                for field, value in expected.items():
                    actual = action_result.get(field)

                    if actual != value:
                        raise RuntimeError(
                            f"Unexpected live repost result for "
                            f"{post_id}: {field} expected {value!r}, "
                            f"got {actual!r}"
                        )

                # Rettiwt confirms the repost but does not return
                # a separate repost ID. Keep the original post ID
                # as the external reference for this action.
                tracker.mark_action_result(
                    post_id,
                    "repost",
                    "completed",
                    external_id=post_id
                )

                print(
                    f"LIVE REPOST CONFIRMED: post={post_id}, "
                    "tracker_status=completed"
                )

                published += 1

            else:
                expected = {
                    "success": True,
                    "dry_run": True,
                    "published": False,
                    "original_post_id": post_id
                }

                for field, value in expected.items():
                    actual = action_result.get(field)

                    if actual != value:
                        raise RuntimeError(
                            f"Unexpected repost dry run result for "
                            f"{post_id}: {field} expected {value!r}, "
                            f"got {actual!r}"
                        )

                updated_post = tracker.get_post(post_id)
                updated_status = (
                    updated_post.get("actions", {})
                    .get("repost", {})
                    .get("status")
                ) if updated_post else None

                if updated_status != "drafted":
                    raise RuntimeError(
                        f"Unexpected tracker state after repost "
                        f"dry run for {post_id}: {updated_status}"
                    )

                print(
                    "Validated: success=True, dry_run=True, "
                    "published=False, tracker_status=drafted"
                )

            tested += 1

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
                            "Repost execution result is uncertain. "
                            f"Details: {error}. "
                            "Manual verification required."
                        )
                    )

                    print(
                        f"Repost result is uncertain for {post_id}. "
                        "Tracker status set to unknown. "
                        "Manual verification is required."
                    )

            raise

    print(f"\nRepost drafts tested successfully: {tested}")

    if live_enabled:
        print(f"Live Reposts confirmed: {published}")
        print(
            f"Maximum reposts per day: {MAX_REPOSTS_PER_RUN}"
        )
    else:
        print("DRY RUN: no reposts were published to X.")
        print("All tested reposts remain in drafted state.")


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


def evaluate_like_candidates(tracker):
    print("\nEvaluating posts for Likes with Gemini...")

    evaluations = load_like_evaluations()
    pending_posts = []

    for post_id, post in tracker.data["posts"].items():
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

    if result is None:
        print(
            "Like evaluation unavailable. "
            "Posts will remain pending for the next execution."
        )
        return None

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
    pattern = r"(?:https?://)?(?:www\.)?(?:" + domains + r")(?:[/:?#]|$)"

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


def generate_like_drafts(tracker, evaluations):
    print("\nGenerating like drafts...")

    # Normalize evaluation IDs.
    evaluations = {
        str(post_id): evaluation
        for post_id, evaluation in evaluations.items()
    }

    # Cancel outdated drafts only when the post was evaluated
    # during the current execution.
    outdated_drafts = 0

    for post_id in tracker.get_drafted_action_ids("like"):
        post_id = str(post_id)

        if post_id not in evaluations:
            continue

        evaluation = evaluations[post_id]

        try:
            score = int(evaluation.get("score", 0))
        except (TypeError, ValueError):
            score = 0

        if (
            evaluation.get("should_like") is not True
            or score < MIN_LIKE_SCORE
        ):
            if tracker.cancel_like_draft(
                post_id,
                reason="No longer approved by current evaluation"
            ):
                outdated_drafts += 1

    print(f"Outdated Like drafts canceled: {outdated_drafts}")

    candidates = []

    for post_id, evaluation in evaluations.items():

        if evaluation.get("should_like") is not True:
            continue

        try:
            score = int(evaluation.get("score", 0))
        except (TypeError, ValueError):
            continue

        if score < MIN_LIKE_SCORE:
            continue

        post = tracker.get_post(post_id)

        if not post:
            continue

        like_action = post.get("actions", {}).get("like", {})
        status = like_action.get("status", "not_started")

        # Never select actions that are already completed,
        # processing, or have an unknown result.
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

            # Platform links retain their special priority.
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

    # Diagnostic: inspect author diversity.
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

    # First pass: balance relationship-building and genuine content.
    for candidate in candidates:

        if len(selected) >= MAX_LIKES_PER_RUN:
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

    # Second pass: fill remaining slots if one category
    # had insufficient eligible candidates.
    if len(selected) < MAX_LIKES_PER_RUN:

        for candidate in candidates:

            if len(selected) >= MAX_LIKES_PER_RUN:
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

    # Save selected drafts.
    for candidate in selected:

        post_id = candidate["post_id"]
        post = tracker.get_post(post_id)
        like_action = post["actions"]["like"]

        # Preserve existing drafts.
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


def load_like_action_config():
    if not ACTION_CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Action config not found: {ACTION_CONFIG_FILE}"
        )

    with ACTION_CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError("Invalid action config")

    like_config = config.get("like")

    if not isinstance(like_config, dict):
        raise ValueError("Missing Like configuration")

    enabled = like_config.get("enabled")

    if not isinstance(enabled, bool):
        raise ValueError("Invalid Like enabled setting")

    return enabled


def load_reply_action_config():
    if not ACTION_CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Action config not found: {ACTION_CONFIG_FILE}"
        )

    with ACTION_CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError("Invalid action config")

    reply_config = config.get("reply")

    if not isinstance(reply_config, dict):
        raise ValueError("Missing Reply configuration")

    enabled = reply_config.get("enabled")

    if not isinstance(enabled, bool):
        raise ValueError("Invalid Reply enabled setting")

    return enabled


def load_repost_action_config():
    if not ACTION_CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Action config not found: {ACTION_CONFIG_FILE}"
        )

    with ACTION_CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError("Invalid action config")

    repost_config = config.get("repost")

    if not isinstance(repost_config, dict):
        raise ValueError("Missing Repost configuration")

    enabled = repost_config.get("enabled")

    if not isinstance(enabled, bool):
        raise ValueError("Invalid Repost enabled setting")

    return enabled


def test_like_drafts(tracker, selected_ids):
    print("\nTesting like drafts with Node.js...")

    enabled = load_like_action_config()

    if enabled:
        print("LIVE LIKE MODE ENABLED")
        print(f"Maximum likes per day: {MAX_LIKES_PER_RUN}")

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

    for post_id in selected_ids:
        post = tracker.get_post(post_id)

        if not post:
            print(f"Like test skipped: post not found ({post_id})")
            continue

        like_action = post.get("actions", {}).get("like", {})
        status = like_action.get("status")

        if status != "drafted":
            print(
                f"Like test skipped for {post_id}: "
                f"status is {status}"
            )
            continue

        request = {"post_id": str(post_id)}

        print(f"\nTesting like for post {post_id}...")

        # DRY RUN: no tracker state transition is necessary.
        if not enabled:
            result = subprocess.run(
                ["node", str(LIKE_ACTION_SCRIPT)],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                check=False
            )

            if result.stderr:
                print(result.stderr.strip())

            if result.returncode != 0:
                raise RuntimeError(
                    f"Node.js like dry run failed for {post_id}: "
                    f"{result.stdout.strip()}"
                )

            try:
                action_result = json.loads(result.stdout.strip())
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"Invalid JSON returned by Node.js for like "
                    f"{post_id}: {result.stdout.strip()}"
                ) from error

            expected = {
                "success": True,
                "dry_run": True,
                "liked": False,
                "original_post_id": str(post_id)
            }

            for field, value in expected.items():
                if action_result.get(field) != value:
                    raise RuntimeError(
                        f"Unexpected like result for {post_id}: "
                        f"{field} expected {value!r}, "
                        f"got {action_result.get(field)!r}"
                    )

            updated_post = tracker.get_post(post_id)
            updated_status = (
                updated_post["actions"]["like"]["status"]
            )

            if updated_status != "drafted":
                raise RuntimeError(
                    f"Unexpected tracker state after Like dry run: "
                    f"{updated_status}"
                )

            print(
                "Validated: success=True, dry_run=True, "
                "liked=False, tracker_status=drafted"
            )

            tested += 1
            continue

        # LIVE EXECUTION:
        # Check the daily quota before persisting processing or calling Node.js.
        allowed, reason = tracker.can_perform_action_today(
            "like",
            MAX_LIKES_PER_RUN
        )

        if not allowed:
            print(f"Like blocked for {post_id}: {reason}")
            break

        # Persist processing BEFORE calling Node.js.
        tracker.mark_action_started(
            post_id,
            "like",
            {
                "post_id": str(post_id),
                "mode": "controlled_live_test",
                "stage": "sending"
            }
        )

        try:
            result = subprocess.run(
                ["node", str(LIKE_ACTION_SCRIPT)],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                check=False
            )

            if result.stderr:
                print(result.stderr.strip())

            if result.returncode != 0:
                raise RuntimeError(
                    f"Node.js Like action failed. "
                    f"Exit code: {result.returncode}. "
                    f"Output: {result.stdout.strip()}"
                )

            try:
                action_result = json.loads(result.stdout.strip())
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"Invalid JSON returned by Node.js: "
                    f"{result.stdout.strip()}"
                ) from error

            expected = {
                "success": True,
                "dry_run": False,
                "liked": True,
                "original_post_id": str(post_id)
            }

            for field, value in expected.items():
                if action_result.get(field) != value:
                    raise RuntimeError(
                        f"Unexpected live Like result: "
                        f"{field} expected {value!r}, "
                        f"got {action_result.get(field)!r}"
                    )

            # Rettiwt confirms the Like, but does not return
            # a separate Like ID. The original post ID is used
            # as the external reference for this action.
            tracker.mark_action_result(
                post_id,
                "like",
                "completed",
                external_id=str(post_id)
            )

            print(
                "LIVE LIKE CONFIRMED: "
                f"post={post_id}, tracker_status=completed"
            )

            tested += 1

        except Exception as error:
            # We cannot safely know whether X received the request.
            # Never retry automatically in this situation.
            current_post = tracker.get_post(post_id)
            current_status = (
                current_post.get("actions", {})
                .get("like", {})
                .get("status")
            )

            if current_status == "processing":
                tracker.mark_action_result(
                    post_id,
                    "like",
                    "unknown",
                    error=str(error)
                )

            print(
                f"Like result is uncertain for {post_id}. "
                "Tracker status set to unknown. "
                "Manual verification is required."
            )

            raise

    if enabled:
        print(f"\nLive Likes confirmed: {tested}")
        print(f"Maximum likes per day: {MAX_LIKES_PER_RUN}")
    else:
        print(f"\nLike drafts tested successfully: {tested}")
        print("DRY RUN: no likes were sent to X.")



def main():
    print("Starting X Agent")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Load posts retrieved by Rettiwt
    posts = load_rettiwt_posts()

    print(f"Loaded {len(posts)} posts from Rettiwt")

    # 2. Save latest scraped posts
    save_json(POSTS_FILE, {
        "account": USERNAME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(posts),
        "posts": posts
    })

    # 3. Load persistent tracker
    tracker = PostTracker()

    # 4. Migrate existing drafts before recovery
    migrate_existing_reply_drafts(tracker)

    # 5. Recover interrupted sending operations
    recover_interrupted_replies(tracker)

    print("\nRecovering interrupted likes...")
    recovered_likes = tracker.recover_interrupted_actions("like")
    print(f"Interrupted likes recovered: {recovered_likes}")

    print("\nRecovering interrupted reposts...")
    recovered_reposts = tracker.recover_interrupted_actions("repost")
    print(f"Interrupted reposts recovered: {recovered_reposts}")

    # 6. Register new posts
    new_ids = tracker.register_posts(posts)

    print(f"New posts registered: {len(new_ids)}")

    # 7. Analyze posts that need analysis
    pending_ids = tracker.get_unanalyzed_ids()

    print(f"Posts pending analysis: {len(pending_ids)}")

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

        result = analyzer.analyze_posts(pending_posts)

        if result is None:
            print(
                "Gemini analysis unavailable. "
                "Posts will remain pending for the next execution."
            )
        else:
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

    else:
        print("No posts require analysis. Skipping Gemini.")

    # 8. Generate ranking from completed analyses
    all_rankings = []

    for post_id, post in tracker.data["posts"].items():
        analysis = post.get("analysis", {})

        if analysis.get("status") == "completed":
            ranking_item = analysis.get("result")

            if ranking_item:
                all_rankings.append(ranking_item)

    all_rankings.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    save_json(RANKING_FILE, {
        "account": USERNAME,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "model": analyzer.model if analyzer else None,
        "total": len(all_rankings),
        "ranking": all_rankings
    })

    print(f"Ranking saved: {len(all_rankings)} analyzed posts")

    # 9. Evaluate reply eligibility
    eligible, blocked = evaluate_reply_eligibility(
        tracker,
        all_rankings
    )

    # 10. Generate and persist reply drafts
    generate_reply_drafts(
        tracker,
        eligible,
        all_rankings
    )

    # 11. Test reply drafts through Node.js
    test_reply_drafts(
        tracker,
        eligible
    )

    # 12. Generate and persist repost drafts
    generate_repost_drafts(
        tracker,
        all_rankings
    )

    # 13. Test repost drafts through Node.js
    test_repost_drafts(
        tracker,
        all_rankings
    )
 
    # 14. Evaluate posts for Likes with the dedicated Gemini prompt
    like_evaluations = evaluate_like_candidates(tracker)

    if like_evaluations is None:
        print(
            "Like processing skipped because Gemini evaluation failed. "
            "Pending posts will be retried in the next execution."
        )
    else:
        # 15. Generate and test like drafts
        like_ids = generate_like_drafts(
            tracker,
            like_evaluations
        )

        test_like_drafts(
            tracker,
            like_ids
        )


    print(f"\nTracker contains {len(tracker.data['posts'])} posts")
    print("\nX Agent completed successfully")



if __name__ == "__main__":
    main()
