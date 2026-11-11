
import process from 'node:process';
import { isActionEnabled } from './action_config.mjs';

async function readInput() {
    let input = '';

    for await (const chunk of process.stdin) {
        input += chunk;
    }

    if (!input.trim()) {
        throw new Error('Empty input');
    }

    return JSON.parse(input);
}

function validateRequest(request) {
    if (!request || typeof request !== 'object') {
        throw new Error('Invalid request: expected an object');
    }

    const postId = String(request.post_id ?? '').trim();
    const replyText = String(request.reply_text ?? '').trim();

    if (!postId) {
        throw new Error('Missing original post ID');
    }

    if (!replyText) {
        throw new Error('Missing reply text');
    }

    if (replyText.length > 280) {
        throw new Error(
            `Reply exceeds 280 characters: ${replyText.length}`
        );
    }

    return { postId, replyText };
}

function outputResult(result) {
    process.stdout.write(`${JSON.stringify(result)}\n`);
}

function getErrorMessage(error) {
    if (error instanceof Error) {
        return error.message;
    }

    return String(error);
}

async function main() {
    let postId = null;
    let requestMayHaveReachedX = false;

    try {
        const request = await readInput();
        const validated = validateRequest(request);

        postId = validated.postId;
        const { replyText } = validated;

        console.error('X Reply Action initialized');
        console.error(`Original post ID: ${postId}`);
        console.error(`Reply length: ${replyText.length}/280`);

        const enabled = await isActionEnabled('reply');

        if (!enabled) {
            console.error('DRY RUN: no request was sent to X.');

            outputResult({
                status: 'dry_run',
                success: true,
                dry_run: true,
                published: false,
                original_post_id: postId,
                reply_text: replyText,
                reply_length: replyText.length,
                reply_id: null,
                error: null
            });

            return;
        }

        const apiKey = process.env.RETTIWT_API_KEY;

        if (!apiKey) {
            throw new Error('Missing RETTIWT_API_KEY');
        }

        const { Rettiwt } = await import('rettiwt-api');

        const rettiwt = new Rettiwt({
            apiKey,
            logging: false
        });

        // From this point onward, a thrown error may mean X received
        // the request even if Rettiwt did not return a response.
        requestMayHaveReachedX = true;

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

        if (
            replyId === undefined ||
            replyId === null ||
            String(replyId).trim() === ''
        ) {
            outputResult({
                status: 'unknown',
                success: false,
                dry_run: false,
                published: null,
                original_post_id: postId,
                reply_id: null,
                error: 'X request returned without a verifiable reply ID'
            });

            return;
        }

        const normalizedReplyId = String(replyId);

        outputResult({
            status: 'completed',
            success: true,
            dry_run: false,
            published: true,
            original_post_id: postId,
            reply_id: normalizedReplyId,
            reply_url: `https://x.com/i/status/${normalizedReplyId}`,
            error: null
        });

    } catch (error) {
        const errorMessage = getErrorMessage(error);

        console.error(`X Reply Action error: ${errorMessage}`);

        const status = requestMayHaveReachedX
            ? 'unknown'
            : 'failed';

        outputResult({
            status,
            success: false,
            dry_run: false,
            published: status === 'failed' ? false : null,
            original_post_id: postId,
            reply_id: null,
            error: errorMessage
        });
    }
}

main().catch((error) => {
    // Last-resort handler. If main unexpectedly rejects, do not
    // pretend that publication definitely failed.
    console.error(`Unexpected X Reply Action error: ${getErrorMessage(error)}`);

    outputResult({
        status: 'unknown',
        success: false,
        dry_run: false,
        published: null,
        original_post_id: null,
        reply_id: null,
        error: getErrorMessage(error)
    });
});
