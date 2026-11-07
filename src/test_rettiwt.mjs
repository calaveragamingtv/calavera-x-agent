
import { Rettiwt } from 'rettiwt-api';
import fs from 'node:fs';

const USERNAME = 'CalaveraGaming';
const LIMIT = 10;

async function main() {
    const apiKey = process.env.RETTIWT_API_KEY;

    if (!apiKey) {
        throw new Error('Missing RETTIWT_API_KEY secret');
    }

    console.log('Initializing Rettiwt...');

    const rettiwt = new Rettiwt({
        apiKey,
        logging: true
    });

    console.log(`Searching user: @${USERNAME}`);

    const user = await rettiwt.user.details(USERNAME);

    if (!user?.id) {
        throw new Error(`User not found: @${USERNAME}`);
    }

    console.log(`User found: ${user.userName}`);
    console.log(`User ID: ${user.id}`);

    console.log(`Fetching last ${LIMIT} posts...`);

    const result = await rettiwt.user.timeline(
        user.id,
        LIMIT
    );

    console.log('Timeline response type:', Array.isArray(result) ? 'array' : typeof result);
    console.log('Timeline response keys:', Object.keys(result ?? {}));

    const tweets = Array.isArray(result)
        ? result
        : Array.isArray(result?.list)
            ? result.list
            : [];

    if (tweets.length === 0) {
        console.log('No tweets found in expected response structure.');
        console.log('Response preview:', JSON.stringify(result, null, 2).slice(0, 5000));
        throw new Error('Timeline returned no recognizable tweets');
    }

    const posts = tweets
        .slice(0, LIMIT)
        .map(tweet => {
            const data = typeof tweet.toJSON === 'function'
                ? tweet.toJSON()
                : tweet;

            return {
                id: data.id,
                text: data.fullText,
                url: data.url,
                created_at: data.createdAt,
                replies: data.replyCount ?? 0,
                reposts: data.retweetCount ?? 0,
                likes: data.likeCount ?? 0,
                views: data.viewCount ?? 0
            };
        });

    fs.mkdirSync('data', { recursive: true });

    fs.writeFileSync(
        'data/rettiwt_posts.json',
        JSON.stringify(posts, null, 2),
        'utf8'
    );

    console.log('================================');
    console.log(`Successfully retrieved ${posts.length} posts`);

    for (const post of posts) {
        console.log('--------------------------------');
        console.log(`ID: ${post.id}`);
        console.log(`Text: ${post.text}`);
        console.log(`Likes: ${post.likes}`);
        console.log(`URL: ${post.url}`);
    }

    console.log('================================');
    console.log('Saved to data/rettiwt_posts.json');
}

main().catch(error => {
    console.error('Rettiwt failed:', error);
    process.exitCode = 1;
});
