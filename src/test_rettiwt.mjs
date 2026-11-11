
import { Rettiwt } from 'rettiwt-api';
import { createDecipheriv } from 'node:crypto';
import fs from 'node:fs';

const ACTION_CONFIG_FILE = 'config/x_actions.json';
const ACCOUNTS_FILE = 'config/monitored_accounts.json';
const OUTPUT_FILE = 'data/rettiwt_posts.json';

const expandedUrlCache = new Map();

function loadScrapingConfig() {
    if (!fs.existsSync(ACTION_CONFIG_FILE)) {
        throw new Error(`Action config not found: ${ACTION_CONFIG_FILE}`);
    }

    const config = JSON.parse(
        fs.readFileSync(ACTION_CONFIG_FILE, 'utf8')
    );

    if (!config || typeof config !== 'object') {
        throw new Error('Invalid action config structure');
    }

    const limit = config.scraping?.posts_per_account;

    if (!Number.isInteger(limit) || limit <= 0) {
        throw new Error(
            'Invalid scraping.posts_per_account configuration'
        );
    }

    return limit;
}

function loadEncryptionKey() {
    const encodedKey = process.env.ACCOUNTS_ENCRYPTION_KEY;

    if (!encodedKey) {
        throw new Error('Missing ACCOUNTS_ENCRYPTION_KEY');
    }

    const key = Buffer.from(encodedKey, 'base64');

    if (key.length !== 32) {
        throw new Error('Invalid AES-256 key length');
    }

    return key;
}

function decryptUsername(encryptedUsername, key) {
    const payload = Buffer.from(encryptedUsername, 'base64');

    if (payload.length < 29) {
        throw new Error('Invalid encrypted username');
    }

    const nonce = payload.subarray(0, 12);
    const authTag = payload.subarray(payload.length - 16);
    const ciphertext = payload.subarray(12, payload.length - 16);

    const decipher = createDecipheriv(
        'aes-256-gcm',
        key,
        nonce
    );

    decipher.setAuthTag(authTag);

    return Buffer.concat([
        decipher.update(ciphertext),
        decipher.final()
    ]).toString('utf8');
}

function loadAccounts() {
    if (!fs.existsSync(ACCOUNTS_FILE)) {
        throw new Error(`Accounts file not found: ${ACCOUNTS_FILE}`);
    }

    const data = JSON.parse(
        fs.readFileSync(ACCOUNTS_FILE, 'utf8')
    );

    if (!Array.isArray(data.accounts)) {
        throw new Error('Invalid monitored accounts structure');
    }

    const enabledAccounts = data.accounts.filter(
        account => account.enabled === true
    );

    if (enabledAccounts.length === 0) {
        throw new Error('No enabled monitored accounts');
    }

    const key = loadEncryptionKey();

    return enabledAccounts.map((account, index) => {
        if (!account.username_encrypted) {
            throw new Error(
                `Missing encrypted username for account ${index + 1}`
            );
        }

        return {
            username: decryptUsername(
                account.username_encrypted,
                key
            ),
            type: account.type || 'unknown'
        };
    });
}

async function expandShortUrl(shortUrl) {
    if (expandedUrlCache.has(shortUrl)) {
        return expandedUrlCache.get(shortUrl);
    }

    const promise = (async () => {
        for (const method of ['HEAD', 'GET']) {
            try {
                const response = await fetch(shortUrl, {
                    method,
                    redirect: 'follow',
                    signal: AbortSignal.timeout(10000)
                });

                const finalUrl = response.url;

                if (method === 'GET') {
                    await response.body?.cancel();
                }

                if (finalUrl && finalUrl !== shortUrl) {
                    return finalUrl;
                }
            } catch (error) {
                if (method === 'GET') {
                    console.warn(
                        `Could not expand URL ${shortUrl}: ${error.message}`
                    );
                }
            }
        }

        return null;
    })();

    expandedUrlCache.set(shortUrl, promise);

    return promise;
}

async function getExpandedUrls(text) {
    const shortUrls = [
        ...new Set(
            (text.match(/https?:\/\/t\.co\/[a-zA-Z0-9]+/g) || [])
        )
    ];

    if (shortUrls.length === 0) {
        return [];
    }

    const results = await Promise.all(
        shortUrls.map(url => expandShortUrl(url))
    );

    return results.filter(Boolean);
}

function logAuthorDiagnostic(data, postId) {
    console.log(`\nAUTHOR DIAGNOSTIC FOR POST ${postId}:`);

    console.log(
        JSON.stringify({
            keys: Object.keys(data),
            tweetBy: data.tweetBy ?? null,
            user: data.user ?? null,
            author: data.author ?? null,
            retweetedTweet: data.retweetedTweet ?? null,
            quotedTweet: data.quotedTweet ?? null
        }, null, 2)
    );
}

function extractTweets(result) {
    if (Array.isArray(result)) {
        return result;
    }

    if (Array.isArray(result?.list)) {
        return result.list;
    }

    return [];
}

async function main() {
    const apiKey = process.env.RETTIWT_API_KEY;

    if (!apiKey) {
        throw new Error('Missing RETTIWT_API_KEY');
    }

    const limit = loadScrapingConfig();
    const accounts = loadAccounts();

    console.log(`Starting X scraper: ${accounts.length} accounts`);
    console.log(`Posts per account configured: ${limit}`);
    console.log(`Maximum posts before deduplication: ${accounts.length * limit}`);

    const rettiwt = new Rettiwt({
        apiKey,
        logging: false
    });

    const postsById = new Map();

    let authorDiagnosticDone = false;
    let totalReturned = 0;
    let totalProcessed = 0;

    for (let index = 0; index < accounts.length; index++) {
        const account = accounts[index];

        console.log(
            `Processing account ${index + 1}/${accounts.length} (${account.type})`
        );

        try {
            const user = await rettiwt.user.details(
                account.username
            );

            if (!user?.id) {
                throw new Error('Account not found');
            }

            const result = await rettiwt.user.timeline(
                user.id,
                limit
            );

            const tweets = extractTweets(result);

            totalReturned += tweets.length;

            console.log(
                `Account ${index + 1}: Rettiwt returned ${tweets.length} posts`
            );

            // Enforce the configured limit even if Rettiwt
            // returns more posts than requested.
            const limitedTweets = tweets.slice(0, limit);

            totalProcessed += limitedTweets.length;

            console.log(
                `Account ${index + 1}: processing ${limitedTweets.length} posts (limit: ${limit})`
            );

            for (const tweet of limitedTweets) {
                const data = typeof tweet.toJSON === 'function'
                    ? tweet.toJSON()
                    : tweet;

                if (!data?.id) {
                    continue;
                }

                const postId = String(data.id);

                if (!authorDiagnosticDone) {
                    logAuthorDiagnostic(data, postId);
                    authorDiagnosticDone = true;
                }

                const postText = data.fullText ?? '';

                const expandedUrls = await getExpandedUrls(postText);

                const monitoredBy = {
                    account_id: String(user.id),
                    account_type: account.type
                };

                if (postsById.has(postId)) {
                    const existing = postsById.get(postId);

                    const alreadyRegistered =
                        existing.monitored_by.some(
                            item => item.account_id === monitoredBy.account_id
                        );

                    if (!alreadyRegistered) {
                        existing.monitored_by.push(monitoredBy);
                    }

                    existing.expanded_urls = [
                        ...new Set([
                            ...(existing.expanded_urls || []),
                            ...expandedUrls
                        ])
                    ];

                    continue;
                }

                postsById.set(postId, {
                    id: postId,

                    author_id: data.tweetBy?.id
                        ? String(data.tweetBy.id)
                        : null,

                    text: postText,
                    url: data.url ?? null,
                    expanded_urls: expandedUrls,
                    created_at: data.createdAt ?? null,

                    replies: data.replyCount ?? 0,
                    reposts: data.retweetCount ?? 0,
                    likes: data.likeCount ?? 0,
                    views: data.viewCount ?? 0,

                    monitored_by: [monitoredBy]
                });
            }

            console.log(
                `Account ${index + 1}: completed successfully`
            );

        } catch (error) {
            console.error(
                `Account ${index + 1} error:`,
                error.message
            );

            throw new Error(
                `Failed to process monitored account ${index + 1}: ${error.message}`
            );
        }
    }

    const posts = Array.from(postsById.values());

    if (posts.length === 0) {
        throw new Error('No posts retrieved from monitored accounts');
    }

    const maximumExpected = accounts.length * limit;

    if (posts.length > maximumExpected) {
        throw new Error(
            `Unexpected post count: ${posts.length}. Maximum expected: ${maximumExpected}`
        );
    }

    fs.mkdirSync('data', {
        recursive: true
    });

    // Replaces the previous scraper output on every execution.
    fs.writeFileSync(
        OUTPUT_FILE,
        JSON.stringify(posts, null, 2),
        'utf8'
    );

    const expandedCount = posts.filter(
        post => post.expanded_urls.length > 0
    ).length;

    const authors = new Set(
        posts
            .map(post => post.author_id)
            .filter(Boolean)
    );

    console.log('========== SCRAPING SUMMARY ==========');
    console.log(`Enabled accounts: ${accounts.length}`);
    console.log(`Configured limit per account: ${limit}`);
    console.log(`Maximum expected before deduplication: ${maximumExpected}`);
    console.log(`Total posts returned by Rettiwt: ${totalReturned}`);
    console.log(`Total posts processed before deduplication: ${totalProcessed}`);
    console.log(`Total unique posts saved: ${posts.length}`);
    console.log(`Posts with expanded URLs: ${expandedCount}`);
    console.log(`Unique author IDs detected: ${authors.size}`);
    console.log(`Posts without author ID: ${posts.filter(post => !post.author_id).length}`);
    console.log(`Posts saved to ${OUTPUT_FILE}`);
    console.log('======================================');
}

main().catch(error => {
    console.error('Scraper failed:', error);
    process.exit(1);
});
