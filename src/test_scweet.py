
import os
import json
from pathlib import Path

from Scweet import Scweet


USERNAME = "CalaveraGaming"
LIMIT = 10


def main():
    auth_token = os.getenv("X_AUTH_TOKEN")

    if not auth_token:
        raise RuntimeError("Missing X_AUTH_TOKEN secret")

    print("Initializing Scweet...")

    scraper = Scweet(
        auth_token=auth_token,
        db_path="scweet_test.db"
    )

    print(f"Getting last {LIMIT} posts from @{USERNAME}...")

    tweets = scraper.get_profile_tweets(
        [USERNAME],
        limit=LIMIT
    )

    posts = []

    for tweet in tweets:
        posts.append({
            "id": str(tweet.get("tweet_id", "")),
            "text": tweet.get("text", ""),
            "url": tweet.get("tweet_url", ""),
            "created_at": tweet.get("timestamp", ""),
            "replies": tweet.get("comments", 0),
            "reposts": tweet.get("retweets", 0),
            "likes": tweet.get("likes", 0),
            "views": tweet.get("views", 0)
        })

    output = Path("data/scweet_posts.json")
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(posts, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("=" * 50)
    print(f"Successfully retrieved {len(posts)} posts")

    for post in posts:
        print("-" * 50)
        print(f"ID: {post['id']}")
        print(f"Text: {post['text']}")
        print(f"Likes: {post['likes']}")
        print(f"URL: {post['url']}")

    print("=" * 50)
    print(f"Saved to {output}")


if __name__ == "__main__":
    main()
