
import process from 'node:process';
import { isActionEnabled } from './action_config.mjs';



async function readInput() {
    let input = '';

    for await (const chunk of process.stdin) {
        input += chunk;
    }

    return JSON.parse(input);
}

function validateRequest(request) {
    const postId = String(request.post_id ?? '').trim();
    const replyText = String(request.reply_text ?? '').trim();

    if (!postId) {
        throw new Error('Missing original post ID');
    }

    if (!replyText) {
        throw new Error('Missing reply text');
    }

    if (replyText.length > 280) {
        throw new Error(`Reply exceeds 280 characters: ${replyText.length}`);
    }

    return { postId, replyText };
}

function outputResult(result) {
    process.stdout.write(JSON.stringify(result) + '\n');
}

async function main() {
    try {
        const request = await readInput();
        const { postId, replyText } = validateRequest(request);

        console.error('X Reply Action initialized');
        console.error(`Original post ID: ${postId}`);
        console.error(`Reply length: ${replyText.length}/280`);
        
        const enabled = await isActionEnabled('reply');
        
        if (!enabled) {
            console.error('DRY RUN: no request was sent to X.');

            outputResult({
                success: true,
                dry_run: true,
                published: false,
                original_post_id: postId,
                reply_text: replyText,
                reply_length: replyText.length
            });

            return;
        }

        const { Rettiwt } = await import('rettiwt-api');

        const apiKey = process.env.RETTIWT_API_KEY;

        if (!apiKey) {
            throw new Error('Missing RETTIWT_API_KEY');
        }

        const rettiwt = new Rettiwt({
            apiKey,
            logging: false
        });

        const result = await rettiwt.tweet.post({
            text: replyText,
            replyTo: postId
        });

        const tweet = typeof result?.toJSON === 'function'
            ? result.toJSON()
            : result;

        const replyId = typeof tweet === 'string'
            ? tweet
            : tweet?.id;

        if (!replyId) {
            throw new Error(
                'Rettiwt did not return a reply ID. Publication status must be verified.'
            );
        }

        outputResult({
            success: true,
            dry_run: false,
            published: true,
            original_post_id: postId,
            reply_id: String(replyId),
            reply_url: tweet?.url ?? `https://x.com/i/status/${replyId}`
        });

    } catch (error) {
        console.error(`X Reply Action error: ${error.message}`);

        outputResult({
            success: false,
            error: error.message
        });

        process.exitCode = 1;
    }
}

main();
