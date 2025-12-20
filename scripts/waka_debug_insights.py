#!/usr/bin/env python3
import os, json, base64
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Load .env file if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, skip .env loading

API_KEY = os.environ["WAKATIME_API_KEY"].strip()
RANGE = os.environ.get("RANGE", "last_year").strip()
URL = f"https://api.wakatime.com/api/v1/users/current/insights/days/{RANGE}"

def auth_header(k: str) -> str:
    return "Basic " + base64.b64encode(f"{k}:".encode()).decode()

req = Request(URL, headers={
    "Authorization": auth_header(API_KEY),
    "Accept": "application/json",
    "User-Agent": "waka-debug"
})

try:
    with urlopen(req, timeout=60) as r:
        payload = json.loads(r.read().decode("utf-8"))
except HTTPError as e:
    print("HTTP ERROR:", e.code)
    print(e.read().decode("utf-8"))
    raise SystemExit(1)

data = payload.get("data", {})
days = data.get("days", [])

print("range:", data.get("range"))
print("start:", data.get("start"))
print("end:", data.get("end"))
print("is_up_to_date:", data.get("is_up_to_date"))
print("days_count:", len(days))

# Print 3 samples so we can see where the total seconds actually lives
for i, d in enumerate(days[:3]):
    print(f"\n--- day sample {i+1} ---")
    print(json.dumps(d, indent=2)[:2500])
