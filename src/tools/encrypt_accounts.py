
import os
import json
import base64
import re
import secrets

from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ACCOUNTS_FILE = Path("config/monitored_accounts.json")


def load_key():
    encoded_key = os.environ.get("ACCOUNTS_ENCRYPTION_KEY")

    if not encoded_key:
        raise RuntimeError("Missing encryption key")

    key = base64.b64decode(encoded_key, validate=True)

    if len(key) != 32:
        raise ValueError("Invalid AES-256 key length")

    return key


def encrypt_username(username, aes):
    nonce = secrets.token_bytes(12)

    encrypted = aes.encrypt(
        nonce,
        username.encode("utf-8"),
        None
    )

    return base64.b64encode(nonce + encrypted).decode("utf-8")


def decrypt_username(encrypted_username, aes):
    payload = base64.b64decode(encrypted_username, validate=True)

    nonce = payload[:12]
    encrypted = payload[12:]

    return aes.decrypt(nonce, encrypted, None).decode("utf-8")


def main():
    username = os.environ["ACCOUNT_USERNAME"].strip().lstrip("@")
    account_type = os.environ["ACCOUNT_TYPE"].strip()

    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
        raise ValueError("Invalid X username")

    allowed_types = {
        "streamer",
        "server",
        "official",
        "company",
        "creator",
        "other"
    }

    if account_type not in allowed_types:
        raise ValueError("Invalid account type")

    # Mask username in subsequent GitHub Actions logs.
    print(f"::add-mask::{username}")

    aes = AESGCM(load_key())

    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    if ACCOUNTS_FILE.exists():
        with ACCOUNTS_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    else:
        data = {"accounts": []}

    if not isinstance(data.get("accounts"), list):
        raise ValueError("Invalid monitored accounts structure")

    # Avoid registering the same username twice.
    for account in data["accounts"]:
        decrypted = decrypt_username(
            account["username_encrypted"],
            aes
        )

        if decrypted.lower() == username.lower():
            raise RuntimeError("Account already registered")

    encrypted_username = encrypt_username(username, aes)

    data["accounts"].append({
        "username_encrypted": encrypted_username,
        "type": account_type,
        "enabled": True
    })

    temporary_file = ACCOUNTS_FILE.with_suffix(".tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")

    temporary_file.replace(ACCOUNTS_FILE)

    print("Account encrypted and registered successfully")
    print(f"Account type: {account_type}")
    print(f"Total monitored accounts: {len(data['accounts'])}")


if __name__ == "__main__":
    main()
