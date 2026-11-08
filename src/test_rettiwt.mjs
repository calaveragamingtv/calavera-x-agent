
import { Rettiwt } from 'rettiwt-api';
import { createDecipheriv } from 'node:crypto';
import fs from 'node:fs';

const LIMIT = 10;
const ACCOUNTS_FILE = 'config/monitored_accounts.json';
const OUTPUT_FILE = 'data/rettiwt_posts.json';

const expandedUrlCache = new Map();

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
    const data = JSON.parse(
        fs.readFileSync(ACCOUNTS_FILE, 'utf8')
    );

    if (!Array.isArray(data.accounts)) {
        throw new Error('Invalid monitored accounts structure');
    }

    const key = loadEncryptionKey();

    return data.accounts
        .filter(account => account.enabled === true)
        .map(account => ({
            username: decryptUsername(
                account.username_encrypted,
                key
            ),
            type: account.type
        }));
}

async function expandShortUrl(shortUrl) {
    if (expandedUrlCache.has(shortUrl)) {
        return expandedUrlCache.get(shortUrl);
    }

    const promise = (async () => {
        for (const method of ['HEAD', 'GET']) {
            let response;

            try {
                response = await fetch(shortUrl, {
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

async function main() {
    const apiKey = process.env.RETTIWT_API_KEY;

    if (!apiKey) {
        throw new Error('Missing RETTIWT_API_KEY');
    }

    const accounts = loadAccounts();

    if (accounts.length === 0) {
        throw new Error('No enabled monitored accounts');
    }

    console.log(`Starting X scraper: ${accounts.length} accounts`);

    const rettiwt = new Rettiwt({
        apiKey,
        logging: false
    });

    const postsById = new Map();

    for (let index = 0; index < accounts.length; index++) {
        const account = accounts[index];

        console.log(
            `Processing account ${index + 1}/${accounts.length} (${account.type})`
        );

        try {
            const user = await rettiwt.user.details(account.username);

            if (!user?.id) {
                throw new Error('Account not found');
            }

            const result = await rettiwt.user.timeline(
                user.id,
                LIMIT
            );

            const tweets = Array.isArray(result)
                ? result
                : Array.isArray(result?.list)
                    ? result.list
                    : [];

            for (const tweet of tweets) {
                const data = typeof tweet.toJSON === 'function'
                    ? tweet.toJSON()
                    : tweet;

                if (!data?.id) {
                    continue;
                }

                const postId = String(data.id);
                const postText = data.fullText ?? '';

                const expandedUrls = await getExpandedUrls(postText);

                const monitoredBy = {
                    account_id: String(user.id),
                    account_type: account.type
                };

                if (postsById.has(postId)) {
                    const existing = postsById.get(postId);

                    const alreadyRegistered = existing.monitored_by.some(
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

            console.log(`Account ${index + 1}: timeline processed`);

        } catch (error) {
            console.error(
                `Account ${index + 1} error:`,
                error.message
            );

            throw new Error(
                `Failed to process monitored account ${index + 1}`
            );
        }
    }

    const posts = Array.from(postsById.values());

    if (posts.length === 0) {
        throw new Error('No posts retrieved from monitored accounts');
    }

    fs.mkdirSync('data', { recursive: true });

    fs.writeFileSync(
        OUTPUT_FILE,
        JSON.stringify(posts, null, 2),
        'utf8'
    );

    const expandedCount = posts.filter(
        post => post.expanded_urls.length > 0
    ).length;

    console.log(`Total unique posts retrieved: ${posts.length}`);
    console.log(`Posts with expanded URLs: ${expandedCount}`);
    console.log(`Posts saved to ${OUTPUT_FILE}`);
}

main().catch(error => {
    console.error(error.message);
    process.exit(1);
});
