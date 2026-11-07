
from src.tools.decrypt_accounts import load_accounts


def main():
    accounts = load_accounts()

    print(f"Successfully decrypted {len(accounts)} accounts")

    for index, account in enumerate(accounts, start=1):
        print(
            f"Account {index}: "
            f"type={account['type']}"
        )


if __name__ == "__main__":
    main()
