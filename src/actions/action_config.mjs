
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const CONFIG_PATH = fileURLToPath(
    new URL('../../config/x_actions.json', import.meta.url)
);

const ACTIONS = ['like', 'reply', 'repost'];

async function loadConfig() {
    const rawConfig = await readFile(CONFIG_PATH, 'utf8');
    const config = JSON.parse(rawConfig);

    if (!config || typeof config !== 'object' || Array.isArray(config)) {
        throw new Error('Invalid x_actions.json structure');
    }

    if (
        !config.actions ||
        typeof config.actions !== 'object' ||
        Array.isArray(config.actions)
    ) {
        throw new Error('Missing or invalid actions configuration');
    }

    if (
        !config.limits ||
        typeof config.limits !== 'object' ||
        Array.isArray(config.limits)
    ) {
        throw new Error('Missing or invalid limits configuration');
    }

    if (
        typeof config.timezone !== 'string' ||
        !config.timezone.trim()
    ) {
        throw new Error('Missing or invalid timezone configuration');
    }

    try {
        new Intl.DateTimeFormat('en-US', {
            timeZone: config.timezone
        });
    } catch {
        throw new Error(
            `Invalid timezone configuration: ${config.timezone}`
        );
    }

    return config;
}

function validateLimit(value, name) {
    if (
        !Number.isInteger(value) ||
        value < 0
    ) {
        throw new Error(`Invalid limit configuration: ${name}`);
    }

    return value;
}

export async function isActionEnabled(action) {
    if (!ACTIONS.includes(action)) {
        throw new Error(`Unsupported action: ${action}`);
    }

    const config = await loadConfig();
    const actionConfig = config.actions[action];

    if (!actionConfig || typeof actionConfig !== 'object') {
        throw new Error(
            `Missing configuration for action: ${action}`
        );
    }

    if (typeof actionConfig.enabled !== 'boolean') {
        throw new Error(
            `Invalid enabled setting for action: ${action}`
        );
    }

    return actionConfig.enabled === true;
}

export async function getActionLimit(action) {
    if (!ACTIONS.includes(action)) {
        throw new Error(`Unsupported action: ${action}`);
    }

    const config = await loadConfig();
    const actionLimits = config.limits[action];

    if (!actionLimits || typeof actionLimits !== 'object') {
        throw new Error(
            `Missing limits configuration for action: ${action}`
        );
    }

    return validateLimit(
        actionLimits.max_per_day,
        `${action}.max_per_day`
    );
}

export async function getReplyAuthorLimit() {
    const config = await loadConfig();
    const replyLimits = config.limits.reply;

    if (!replyLimits || typeof replyLimits !== 'object') {
        throw new Error('Missing Reply limits configuration');
    }

    return validateLimit(
        replyLimits.max_per_author_per_day,
        'reply.max_per_author_per_day'
    );
}

export async function getConfiguredTimezone() {
    const config = await loadConfig();

    return config.timezone;
}
