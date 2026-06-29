import requests

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

session = requests.Session()
r1 = session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
print(f"Homepage: {r1.status_code}")

r2 = session.get("https://www.nseindia.com/api/quote-equity?symbol=INDIGO", headers=NSE_HEADERS, timeout=10)
print(f"Status: {r2.status_code}")
print(f"Response: {r2.text[:300]}")	