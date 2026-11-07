
import { Rettiwt } from 'rettiwt-api';

async function main() {
    let input = '';

    for await (const chunk of process.stdin) {
        input += chunk;
    }

    const request = JSON.parse(input);

    const postId = String(request.post_id ?? '').trim();
    const replyText = String(request.reply_text ?? '').trim();

    if (!postId) {
        throw new Error('Missing post_id');
    }

    if (!replyText) {
        throw new Error('Missing reply_text');
    }

    if (replyText.length > 280) {
        throw new Error('Reply exceeds 280 characters');
    }

    // Explicit execution guard.
    // Prevents accidental publication during development.
    if (request.execute !== true) {
        throw new Error('Execution not explicitly authorized');
    }

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

    console.log(JSON.stringify({
        success: true,
        original_post_id: postId,
        reply_id: tweet?.id ? String(tweet.id) : null,
        reply_url: tweet?.url ?? null
    }));
}

main().catch(error => {
    console.error(JSON.stringify({
        success: false,
        error: error.message
    }));

    process.exit(1);
});
