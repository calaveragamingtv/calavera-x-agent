```python
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timezone

from twscrape import API


# Configuration
USERNAME = "CalaveraGaming"
LIMIT = 10

DATA_FILE = Path("data/posts.json")
DB_FILE = "accounts.db"


async def scrape_posts():

    auth_token = os.environ.get("X_AUTH_TOKEN")
    ct0 = os.environ.get("X_CT0")

    if not auth_token or not ct0:
        raise RuntimeError(
            "Missing X_AUTH_TOKEN or X_CT0 environment variables"
        )

    api = API(DB_FILE)

    # Prepare cookies
    cookies = json.dumps({
        "auth_token": auth_token,
        "ct0": ct0
    })

    # Register X account
    await api.pool.add_account(
        username="scraper_account",
        password="",
        email="",
        email_password="",
        cookies=cookies
    )

    print(f"Searching X user: @{USERNAME}")

    # Get user
    user = await api.user_by_login(USERNAME)

    if not user:
        raise RuntimeError(f"X user not found: @{USERNAME}")

    print(f"User found: {user.username} (ID: {user.id})")

    # Retrieve latest posts
    tweets = []

    async for tweet in api.user_tweets(user.id, limit=LIMIT):

        tweets.append({
            "id": str(tweet.id),
            "text": tweet.rawContent,
            "url": f"https://x.com/{USERNAME}/status/{tweet.id}",
            "created_at": tweet.date.isoformat(),
            "replies": tweet.replyCount,
            "reposts": tweet.retweetCount,
            "likes": tweet.likeCount,
            "views": tweet.viewCount
        })

        if len(tweets) >= LIMIT:
            break

    # Prepare JSON
    output = {
        "account": USERNAME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(tweets),
        "posts": tweets
    }

    # Create directory
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Save JSON
    with DATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)

    print(f"Successfully saved {len(tweets)} posts to {DATA_FILE}")


if __name__ == "__main__":
    asyncio.run(scrape_posts())
```
