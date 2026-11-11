
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

    if (!postId) {
        throw new Error('Missing original post ID');
    }

    return { postId };
}

function outputResult(result) {
    process.stdout.write(`${JSON.stringify(result)}\n`);
}

function getErrorMessage(error) {
    return error instanceof Error
        ? error.message
        : String(error);
}

async function main() {
    let postId = null;
    let requestMayHaveReachedX = false;

    try {
        const request = await readInput();
        ({ postId } = validateRequest(request));

        console.error('X Like Action initialized');
        console.error(`Original post ID: ${postId}`);

        const enabled = await isActionEnabled('like');

        if (!enabled) {
            console.error('DRY RUN: no request was sent to X.');

            outputResult({
                status: 'dry_run',
                success: true,
                dry_run: true,
                liked: false,
                original_post_id: postId,
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

        // From this point, the request may reach X.
        requestMayHaveReachedX = true;

        const result = await rettiwt.tweet.like(postId);

        if (result !== true) {
            outputResult({
                status: 'unknown',
                success: false,
                dry_run: false,
                liked: null,
                original_post_id: postId,
                error: 'Rettiwt did not confirm the like'
            });

            return;
        }

        outputResult({
            status: 'completed',
            success: true,
            dry_run: false,
            liked: true,
            original_post_id: postId,
            error: null
        });

    } catch (error) {
        const errorMessage = getErrorMessage(error);

        console.error(`X Like Action error: ${errorMessage}`);

        const status = requestMayHaveReachedX
            ? 'unknown'
            : 'failed';

        outputResult({
            status,
            success: false,
            dry_run: false,
            liked: status === 'failed' ? false : null,
            original_post_id: postId,
            error: errorMessage
        });
    }
}

main().catch((error) => {
    console.error(`Unexpected X Like error: ${getErrorMessage(error)}`);

    outputResult({
        status: 'unknown',
        success: false,
        dry_run: false,
        liked: null,
        original_post_id: null,
        error: getErrorMessage(error)
    });
});
