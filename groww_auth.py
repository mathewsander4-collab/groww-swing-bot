"""
Groww authentication — two approaches:

APPROACH 1: API Key + Secret (what you currently have)
- Requires daily manual approval on groww.in/trade-api/api-keys
- Good for testing, annoying for daily use

APPROACH 2: TOTP (recommended for automation)
- Generate once from groww.in/trade-api/api-keys → "Generate TOTP Token"
- No daily approval needed — fully automated
- Requires: pip install pyotp

Usage:
    python groww_auth.py                     # uses API Key + Secret from env
    python groww_auth.py --totp              # uses TOTP flow (automated)
"""
import json
import os
import argparse

from growwapi import GrowwAPI
import config

TOKEN_FILE = os.path.join(config.DATA_DIR, "groww_token.json")


def get_token_via_key_secret() -> str:
    """
    Approach 1: API Key + Secret.
    Requires daily approval on Groww website before running.
    """
    api_key = config.GROWW_API_KEY
    secret   = config.GROWW_API_SECRET

    if not api_key or not secret:
        raise SystemExit(
            "Set environment variables first:\n"
            "  set GROWW_API_KEY=your_api_key\n"
            "  set GROWW_API_SECRET=your_api_secret"
        )

    print("Getting access token via API Key + Secret...")
    print("NOTE: Make sure you approved the session today on groww.in/trade-api/api-keys")
    access_token = GrowwAPI.get_access_token(api_key=api_key, secret=secret)
    return access_token


def get_token_via_totp() -> str:
    """
    Approach 2: TOTP — fully automated, no daily approval needed.
    Requires GROWW_TOTP_TOKEN and GROWW_TOTP_SECRET env variables.
    """
    try:
        import pyotp
    except ImportError:
        raise SystemExit("Install pyotp first: pip install pyotp")

    totp_token  = config.GROWW_TOTP_TOKEN
    totp_secret = config.GROWW_TOTP_SECRET

    if not totp_token or not totp_secret:
        raise SystemExit(
            "Set environment variables first:\n"
            "  set GROWW_TOTP_TOKEN=your_totp_token\n"
            "  set GROWW_TOTP_SECRET=your_totp_secret\n\n"
            "Get these from groww.in/trade-api/api-keys\n"
            "→ Click dropdown next to 'Generate API Key'\n"
            "→ Select 'Generate TOTP Token'"
        )

    totp_gen = pyotp.TOTP(totp_secret)
    totp_code = totp_gen.now()
    print(f"Generating access token via TOTP (code: {totp_code})...")
    access_token = GrowwAPI.get_access_token(api_key=totp_token, totp=totp_code)
    return access_token


def save_token(access_token: str):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump({"access_token": access_token}, f)
    print(f"Access token saved to {TOKEN_FILE}")


def load_token() -> str:
    if not os.path.exists(TOKEN_FILE):
        raise FileNotFoundError(
            "No token found. Run first:\n"
            "  python groww_auth.py          (API Key + Secret)\n"
            "  python groww_auth.py --totp   (TOTP, recommended)"
        )
    with open(TOKEN_FILE) as f:
        data = json.load(f)
    return data["access_token"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--totp", action="store_true", help="Use TOTP flow (no daily approval needed)")
    args = parser.parse_args()

    if args.totp:
        token = get_token_via_totp()
    else:
        token = get_token_via_key_secret()

    save_token(token)

    # Quick test
    groww = GrowwAPI(token)
    profile = groww.get_user_profile()
    print(f"Logged in successfully!")
    print(f"Token ready — run: python main.py scan OR python main.py backtest --years 3")
