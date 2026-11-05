
import os
from twscrape import API


class XScraper:

    def __init__(self, db_file="twscrape.db"):
        self.db_file = db_file
        self.api = API(db_file)
        self.authenticated = False

    async def authenticate(self):
        auth_token = os.getenv("X_AUTH_TOKEN")
        ct0 = os.getenv("X_CT0")
        username = os.getenv("X_ACCOUNT_USERNAME")

        if not auth_token or not ct0 or not username:
            raise RuntimeError("Missing X authentication secrets")

        cookies = f"auth_token={auth_token}; ct0={ct0}"

        await self.api.pool.add_account_cookies(
            username=username,
            cookies=cookies
        )

        self.authenticated = True
        print("X authentication configured successfully")

    async def get_user_posts(self, username, limit=10):
        if not self.authenticated:
            raise RuntimeError("XScraper is not authenticated")

        if limit < 1:
            raise ValueError("limit must be greater than zero")

        print(f"Searching X user: @{username}")

        user = await self.api.user_by_login(username)

        if not user:
            raise RuntimeError(f"X user not found: @{username}")

        print(f"User found: {user.username} (ID: {user.id})")

        posts = []

        async for tweet in self.api.user_tweets(user.id, limit=limit):
            posts.append({
                "id": str(tweet.id),
                "text": tweet.rawContent,
                "url": f"https://x.com/{username}/status/{tweet.id}",
                "created_at": tweet.date.isoformat(),
                "replies": tweet.replyCount,
                "reposts": tweet.retweetCount,
                "likes": tweet.likeCount,
                "views": tweet.viewCount
            })

            if len(posts) >= limit:
                break

        print(f"Successfully retrieved {len(posts)} posts")

        return posts
