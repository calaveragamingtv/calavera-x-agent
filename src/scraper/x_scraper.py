import os

from twscrape import API


class XScraper:

    def __init__(self, db_file="accounts.db"):
        self.db_file = db_file
        self.api = API(db_file)

    async def authenticate(self):
        auth_token = os.environ.get("X_AUTH_TOKEN")
        ct0 = os.environ.get("X_CT0")
        username = os.environ.get("X_ACCOUNT_USERNAME")

        if not auth_token or not ct0 or not username:
            raise RuntimeError(
                "Missing X authentication environment variables"
            )

        cookies = f"auth_token={auth_token}; ct0={ct0}"

        await self.api.pool.add_account_cookies(
            username=username,
            cookies=cookies
        )

    async def get_user_posts(self, username, limit=10):
        if limit < 1:
            raise ValueError("limit must be greater than zero")

        user = await self.api.user_by_login(username)

        if not user:
            raise RuntimeError(f"X user not found: @{username}")

        posts = []

        async for tweet in self.api.user_tweets(
            user.id,
            limit=limit
        ):
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

        return posts
