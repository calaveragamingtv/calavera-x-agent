import json
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone

from src.ai.gemini_analyzer import GeminiAnalyzer
from src.ai.gemini_reply_generator import GeminiReplyGenerator
from src.post_tracker import PostTracker


USERNAME = "monitored_accounts"
LIMIT = 10
MAX_TOTAL_REPLIES = 2

MAX_LIKES_PER_RUN = 2
MIN_LIKE_SCORE = 70

DATA_DIR = Path("data")
POSTS_FILE = DATA_DIR / "posts.json"
RETTIWT_FILE = DATA_DIR / "rettiwt_posts.json"
RANKING_FILE = DATA_DIR / "ranking.json"

REPLY_ACTION_SCRIPT = Path("src/actions/x_reply.mjs")
REPOST_ACTION_SCRIPT = Path("src/actions/x_repost.mjs")
LIKE_ACTION_SCRIPT = Path("src/actions/x_like.mjs")


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

        if (
            usage["total_replies"] + planned_total
            >= MAX_TOTAL_REPLIES
        ):
            blocked.append((post_id, "global_test_limit_reached"))
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

    tested = 0

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

        payload = reply_action.get("payload") or {}
        reply_text = payload.get("reply_text")

        if not isinstance(reply_text, str) or not reply_text.strip():
            raise RuntimeError(
                f"Invalid reply draft for post {post_id}"
            )

        request = {
            "post_id": post_id,
            "reply_text": reply_text
        }

        print(f"\nTesting reply for post {post_id}...")

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
                f"Node.js reply dry run failed for post {post_id}. "
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
                f"Unexpected tracker state after dry run for {post_id}: "
                f"{updated_status}"
            )

        print(
            "Validated: "
            "success=True, dry_run=True, published=False, "
            "tracker_status=drafted"
        )

        tested += 1

    print(f"\nReply drafts tested successfully: {tested}")
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

    candidates = [
        item
        for item in rankings
        if item.get("recommended_action") == "repost"
    ]

    tested = 0

    for item in candidates:
        post_id = str(item.get("post_id", "")).strip()
        post = tracker.get_post(post_id)

        if not post:
            continue

        repost_action = post["actions"]["repost"]

        if repost_action["status"] != "drafted":
            continue

        request = {"post_id": post_id}

        print(f"\nTesting repost for post {post_id}...")

        result = subprocess.run(
            ["node", str(REPOST_ACTION_SCRIPT)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            check=False
        )

        if result.stderr:
            print(result.stderr.strip())

        if result.returncode != 0:
            raise RuntimeError(
                f"Node.js repost dry run failed for {post_id}: "
                f"{result.stdout.strip()}"
            )

        try:
            action_result = json.loads(result.stdout.strip())
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"Invalid JSON returned by Node.js for repost {post_id}"
            ) from error

        expected = {
            "success": True,
            "dry_run": True,
            "published": False,
            "original_post_id": post_id
        }

        for field, value in expected.items():
            if action_result.get(field) != value:
                raise RuntimeError(
                    f"Unexpected repost result for {post_id}: "
                    f"{field} expected {value!r}, "
                    f"got {action_result.get(field)!r}"
                )

        updated_post = tracker.get_post(post_id)
        updated_status = updated_post["actions"]["repost"]["status"]

        if updated_status != "drafted":
            raise RuntimeError(
                f"Unexpected tracker state after repost dry run: "
                f"{updated_status}"
            )

        print(
            "Validated: success=True, dry_run=True, "
            "published=False, tracker_status=drafted"
        )

        tested += 1

    print(f"\nRepost drafts tested successfully: {tested}")
    print("DRY RUN: no reposts were published to X.")


def is_stream_announcement(text):
    if not isinstance(text, str) or not text.strip():
        return False

    normalized = text.lower()

    patterns = [
        r"\bestoy en directo\b",
        r"\bestamos en directo\b",
        r"\ben directo\b",
        r"\bestoy en vivo\b",
        r"\bestamos en vivo\b",
        r"\ben vivo\b",
        r"\bya estamos live\b",
        r"\bestoy live\b",
        r"\bestamos live\b",
        r"\bi['’]m live\b",
        r"\bwe['’]re live\b",
        r"\blive now\b",
        r"\bstream is live\b",
        r"\bgoing live\b",
        r"\bstreaming now\b",
    ]

    return any(
        re.search(pattern, normalized)
        for pattern in patterns
    )


def generate_like_drafts(tracker, rankings):
    print("\nGenerating like drafts...")

    candidates_by_id = {}

    # Stream announcements have priority over regular interesting posts.
    for post_id, post in tracker.data["posts"].items():
        if is_stream_announcement(post.get("text", "")):
            candidates_by_id[str(post_id)] = {
                "post_id": str(post_id),
                "reason": "stream_announcement",
                "score": 101
            }

    # Add interesting posts recommended by Gemini.
    for item in rankings:
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            continue

        if score < MIN_LIKE_SCORE:
            continue

        post_id = str(item.get("post_id", "")).strip()

        if not post_id:
            continue

        if post_id not in candidates_by_id:
            candidates_by_id[post_id] = {
                "post_id": post_id,
                "reason": "interesting_post",
                "score": score
            }

    candidates = sorted(
        candidates_by_id.values(),
        key=lambda item: item["score"],
        reverse=True
    )

    eligible = []

    for candidate in candidates:
        post_id = candidate["post_id"]
        post = tracker.get_post(post_id)

        if not post:
            continue

        like_action = post["actions"]["like"]
        status = like_action["status"]

        if status in ("completed", "unknown", "processing"):
            continue

        if status not in ("not_started", "failed", "drafted"):
            continue

        eligible.append(candidate)

    selected = eligible[:MAX_LIKES_PER_RUN]

    print(f"Like candidates: {len(candidates)}")
    print(f"Eligible likes: {len(eligible)}")
    print(f"Selected this execution: {len(selected)}")

    for candidate in selected:
        post_id = candidate["post_id"]
        post = tracker.get_post(post_id)
        like_action = post["actions"]["like"]

        if like_action["status"] == "drafted":
            print(f"Existing like draft preserved: {post_id}")
            continue

        payload = {
            "post_id": post_id,
            "reason": candidate["reason"],
            "score": candidate["score"],
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


def test_like_drafts(tracker, selected_ids):
    print("\nTesting like drafts with Node.js...")

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

        like_action = post["actions"]["like"]

        if like_action["status"] != "drafted":
            print(
                f"Like test skipped for {post_id}: "
                f"status is {like_action['status']}"
            )
            continue

        request = {"post_id": post_id}

        print(f"\nTesting like for post {post_id}...")

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
                f"Invalid JSON returned by Node.js for like {post_id}: "
                f"{result.stdout.strip()}"
            ) from error

        expected = {
            "success": True,
            "dry_run": True,
            "liked": False,
            "original_post_id": post_id
        }

        for field, value in expected.items():
            if action_result.get(field) != value:
                raise RuntimeError(
                    f"Unexpected like result for {post_id}: "
                    f"{field} expected {value!r}, "
                    f"got {action_result.get(field)!r}"
                )

        updated_post = tracker.get_post(post_id)
        updated_status = updated_post["actions"]["like"]["status"]

        if updated_status != "drafted":
            raise RuntimeError(
                f"Unexpected tracker state after like dry run: "
                f"{updated_status}"
            )

        print(
            "Validated: success=True, dry_run=True, "
            "liked=False, tracker_status=drafted"
        )

        tested += 1

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

    # 11. Test reply drafts through Node.js (DRY RUN)
    test_reply_drafts(
        tracker,
        eligible
    )

    # 12. Generate and persist repost drafts
    generate_repost_drafts(
        tracker,
        all_rankings
    )

    # 13. Test repost drafts through Node.js (DRY RUN)
    test_repost_drafts(
        tracker,
        all_rankings
    )

    # 14. Generate and test like drafts (DRY RUN)
    like_ids = generate_like_drafts(
        tracker,
        all_rankings
    )

    test_like_drafts(
        tracker,
        like_ids
    )

    print(f"\nTracker contains {len(tracker.data['posts'])} posts")
    print("\nX Agent completed successfully")


if __name__ == "__main__":
    main()
