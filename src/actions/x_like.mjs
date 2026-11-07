import process from 'node:process';

const REAL_LIKING_ENABLED = false;

async function readInput() {
    let input = '';

    for await (const chunk of process.stdin) {
        input += chunk;
    }

    return JSON.parse(input);
}

function validateRequest(request) {
    const postId = String(request.post_id ?? '').trim();

    if (!postId) {
        throw new Error('Missing original post ID');
    }

    return { postId };
}

function outputResult(result) {
    process.stdout.write(JSON.stringify(result) + '\n');
}

async function main() {
    try {
        const request = await readInput();
        const { postId } = validateRequest(request);

        console.error('X Like Action initialized');
        console.error(`Original post ID: ${postId}`);

        if (!REAL_LIKING_ENABLED) {
            console.error('DRY RUN: no request was sent to X.');

            outputResult({
                success: true,
                dry_run: true,
                liked: false,
                original_post_id: postId
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

        const result = await rettiwt.tweet.like(postId);

        if (result !== true) {
            throw new Error('Rettiwt did not confirm the like');
        }

        outputResult({
            success: true,
            dry_run: false,
            liked: true,
            original_post_id: postId
        });

    } catch (error) {
        console.error(`X Like Action error: ${error.message}`);

        outputResult({
            success: false,
            error: error.message
        });

        process.exitCode = 1;
    }
}

main();
