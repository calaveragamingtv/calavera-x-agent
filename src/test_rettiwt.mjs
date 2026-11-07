
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

    const tweets = result?.data ?? [];

    const posts = tweets.slice(0, LIMIT).map(tweet => ({
        id: tweet.id,
        text: tweet.fullText,
        url: tweet.url,
        created_at: tweet.createdAt,
        replies: tweet.replyCount ?? 0,
        reposts: tweet.retweetCount ?? 0,
        likes: tweet.likeCount ?? 0,
        views: tweet.viewCount ?? 0
    }));

    fs.mkdirSync('data', { recursive: true });

    fs.writeFileSync(
        'data/rettiwt_posts.json',
        JSON.stringify(posts, null, 2),
        'utf8'
    );

    console.log('================================');
    console.log(`Retrieved ${posts.length} posts`);

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
