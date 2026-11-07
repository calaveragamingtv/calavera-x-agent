
import process from 'node:process';

const REAL_POSTING_ENABLED = false;

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
        throw new Error(
            `Reply exceeds 280 characters: ${replyText.length}`
        );
    }

    return {
        postId,
        replyText
    };
}

async function main() {
    const request = await readInput();

    const { postId, replyText } = validateRequest(request);

    console.log('X Reply Action initialized');
    console.log(`Original post ID: ${postId}`);
    console.log(`Reply length: ${replyText.length}/280`);
    console.log(`Reply text: ${replyText}`);

    if (!REAL_POSTING_ENABLED) {
        console.log('DRY RUN: real posting is disabled.');
        console.log('No request was sent to X.');

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

    const replyId = await rettiwt.tweet.post({
        text: replyText,
        replyTo: postId
    });

    if (!replyId) {
        throw new Error(
            'Rettiwt did not return a reply ID. Publication status must be verified.'
        );
    }

    console.log(JSON.stringify({
        success: true,
        original_post_id: postId,
        reply_id: String(replyId),
        reply_url: `https://x.com/i/status/${replyId}`
    }));
}

main().catch(error => {
    console.error(JSON.stringify({
        success: false,
        error: error.message
    }));

    process.exit(1);
});
