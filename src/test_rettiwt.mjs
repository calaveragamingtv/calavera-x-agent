
import { Rettiwt } from 'rettiwt-api';
import { createDecipheriv } from 'node:crypto';
import fs from 'node:fs';

const LIMIT = 10;
const ACCOUNTS_FILE = 'config/monitored_accounts.json';
const OUTPUT_FILE = 'data/rettiwt_posts.json';

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
                
                if (!globalThis.authorDebugDone) {
                    globalThis.authorDebugDone = true;
                
                    console.log('RAW TWEET FIELDS:', Object.keys(data));
                
                    for (const [key, value] of Object.entries(data)) {
                        if (value && typeof value === 'object') {
                            console.log(
                                `NESTED FIELD ${key}:`,
                                Object.keys(value)
                            );
                        }
                    }
                }

                if (!data?.id) {
                    continue;
                }

                const postId = String(data.id);

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

                    continue;
                }

                postsById.set(postId, {
                    id: postId,
                    text: data.fullText ?? '',
                    url: data.url ?? null,
                    created_at: data.createdAt ?? null,
                    replies: data.replyCount ?? 0,
                    reposts: data.retweetCount ?? 0,
                    likes: data.likeCount ?? 0,
                    views: data.viewCount ?? 0,
                    monitored_by: [monitoredBy]
                });
            }

            console.log(`Account ${index + 1}: timeline processed`);

        } catch {
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

    console.log(`Total unique posts retrieved: ${posts.length}`);
    console.log(`Posts saved to ${OUTPUT_FILE}`);
}

main().catch(error => {
    console.error(error.message);
    process.exit(1);
});
