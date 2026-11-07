
import os
import base64
import getpass
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def load_key():
    encoded_key = os.getenv("ACCOUNTS_ENCRYPTION_KEY")

    if not encoded_key:
        raise RuntimeError("Missing ACCOUNTS_ENCRYPTION_KEY")

    key = base64.b64decode(encoded_key, validate=True)

    if len(key) != 32:
        raise ValueError("AES-256 requires exactly 32 bytes")

    return key


def encrypt_username(username: str, aes: AESGCM) -> str:
    nonce = secrets.token_bytes(12)

    encrypted = aes.encrypt(
        nonce,
        username.strip().encode("utf-8"),
        None
    )

    payload = nonce + encrypted

    return base64.b64encode(payload).decode("utf-8")


def main():
    key = load_key()
    aes = AESGCM(key)

    print("AES-256-GCM username encryptor")
    print("Enter usernames. Leave empty to finish.\n")

    accounts = []

    while True:
        username = getpass.getpass("Username: ").strip()

        if not username:
            break

        encrypted = encrypt_username(username, aes)

        accounts.append({
            "username_encrypted": encrypted,
            "type": "streamer",
            "enabled": True
        })

    print("\nEncrypted accounts:\n")

    for account in accounts:
        print(account)


if __name__ == "__main__":
    main()
