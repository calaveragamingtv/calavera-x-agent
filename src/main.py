
import json
from pathlib import Path
from datetime import datetime, timezone

from src.ai.gemini_analyzer import GeminiAnalyzer
from src.post_tracker import PostTracker


USERNAME = "monitored_accounts"
LIMIT = 10
MAX_TOTAL_REPLIES = 2

DATA_DIR = Path("data")
POSTS_FILE = DATA_DIR / "posts.json"
RETTIWT_FILE = DATA_DIR / "rettiwt_posts.json"
RANKING_FILE = DATA_DIR / "ranking.json"


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

        # Reserve the slot during this evaluation.
        # This prevents multiple posts from the same author
        # from being considered eligible in the same run.
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

    print("Dry run only: no actions sent to X.")

    return eligible, blocked


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

    # 3. Register posts in persistent tracker
    tracker = PostTracker()

    new_ids = tracker.register_posts(posts)

    print(f"New posts registered: {len(new_ids)}")

    # 4. Get posts that still need analysis
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

            # 5. Persist each successful analysis
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

    # 6. Generate ranking from all completed analyses
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

    # 7. Evaluate reply eligibility without executing actions
    eligible, blocked = evaluate_reply_eligibility(
        tracker,
        all_rankings
    )

    print(f"Tracker contains {len(tracker.data['posts'])} posts")
    print("\nX Agent completed successfully")


if __name__ == "__main__":
    main()
