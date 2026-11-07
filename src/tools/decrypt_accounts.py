
import os
import json
import base64

from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ACCOUNTS_FILE = Path("config/monitored_accounts.json")


def load_key():
    encoded_key = os.environ.get("ACCOUNTS_ENCRYPTION_KEY")

    if not encoded_key:
        raise RuntimeError("Missing ACCOUNTS_ENCRYPTION_KEY")

    key = base64.b64decode(encoded_key, validate=True)

    if len(key) != 32:
        raise ValueError("Invalid AES-256 key length")

    return key


def decrypt_username(encrypted_username, aes):
    payload = base64.b64decode(encrypted_username, validate=True)

    if len(payload) < 29:
        raise ValueError("Invalid encrypted username")

    nonce = payload[:12]
    encrypted = payload[12:]

    return aes.decrypt(
        nonce,
        encrypted,
        None
    ).decode("utf-8")


def load_accounts():
    if not ACCOUNTS_FILE.exists():
        raise FileNotFoundError(
            f"Accounts file not found: {ACCOUNTS_FILE}"
        )

    with ACCOUNTS_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data.get("accounts"), list):
        raise ValueError("Invalid monitored accounts structure")

    aes = AESGCM(load_key())
    accounts = []

    for account in data["accounts"]:
        if not account.get("enabled", False):
            continue

        username = decrypt_username(
            account["username_encrypted"],
            aes
        )

        accounts.append({
            "username": username,
            "type": account["type"]
        })

    return accounts
