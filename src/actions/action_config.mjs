import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const CONFIG_PATH = fileURLToPath(
    new URL('../../config/x_actions.json', import.meta.url)
);

export async function isActionEnabled(action) {
    const rawConfig = await readFile(CONFIG_PATH, 'utf8');
    const config = JSON.parse(rawConfig);

    if (!Object.hasOwn(config, action)) {
        throw new Error(`Missing configuration for action: ${action}`);
    }

    if (typeof config[action].enabled !== 'boolean') {
        throw new Error(
            `Invalid enabled setting for action: ${action}`
        );
    }

    return config[action].enabled === true;
}
