import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

session = requests.Session()
session.get("https://www.nseindia.com", headers=headers)

r = session.get("https://www.nseindia.com/api/allIndices", headers=headers)
print(r.status_code)
print(r.text[:500])