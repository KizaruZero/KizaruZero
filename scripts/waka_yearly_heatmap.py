#!/usr/bin/env python3
import os
import time
import math
import base64
import json
import datetime as dt
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

WAKATIME_API_KEY = os.environ["WAKATIME_API_KEY"].strip()
RANGE = os.environ.get("RANGE", "last_year").strip()  # last_30_days, last_6_months, last_year, etc.
URL = f"https://api.wakatime.com/api/v1/users/current/insights/days/{RANGE}"
OUT_PATH = os.environ.get("OUT_PATH", f"assets/waka-heatmap-{RANGE}.svg")

def basic_auth_header(api_key: str) -> str:
    # Basic base64(API_KEY:)
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("utf-8")
    return f"Basic {token}"

def fetch_json_with_retry(max_tries=12, sleep_seconds=25):
    """
    Retries when response is stale (is_up_to_date=false) or when temporary network errors happen.
    If WakaTime returns 402, we raise with a clear message.
    """
    headers = {
        "Authorization": basic_auth_header(WAKATIME_API_KEY),
        "Accept": "application/json",
        "User-Agent": "waka-heatmap-github-action"
    }

    last_payload = None
    for i in range(1, max_tries + 1):
        try:
            req = Request(URL, headers=headers)
            with urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            last_payload = payload

            data = payload.get("data", {})
            # Some endpoints might not include is_up_to_date; treat as ready
            if data.get("is_up_to_date", True):
                return data

            pct = data.get("percent_calculated", 0)
            status = data.get("status", "stale")
            print(f"[retry {i}/{max_tries}] stale: status={status}, percent={pct}%. Sleeping {sleep_seconds}s...")
            time.sleep(sleep_seconds)

        except HTTPError as e:
            if e.code == 402:
                raise RuntimeError(
                    f"WakaTime API returned 402 PAYMENT REQUIRED for range '{RANGE}'. "
                    f"Your account appears not to have access to that time_range. "
                    f"Try RANGE=last_year/last_6_months/last_30_days."
                ) from e
            if e.code in (429, 500, 502, 503, 504):
                print(f"[retry {i}/{max_tries}] HTTP {e.code}. Sleeping {sleep_seconds}s...")
                time.sleep(sleep_seconds)
                continue
            raise

        except URLError as e:
            print(f"[retry {i}/{max_tries}] Network error: {e}. Sleeping {sleep_seconds}s...")
            time.sleep(sleep_seconds)

    print("Warning: insights still stale after retries. Rendering last available data (if any).")
    if last_payload and "data" in last_payload:
        return last_payload["data"]
    return {}

def parse_days_insight(data):
    """
    Expecting data["days"] list. Each item usually includes:
      - date (YYYY-MM-DD)
      - total_seconds or grand_total.total_seconds
    Output: dict date -> total_seconds
    """
    days = data.get("days", [])
    date_to_seconds = {}

    for item in days:
        date_str = item.get("date") or item.get("range", {}).get("date")
        total_seconds = (
            item.get("total_seconds")
            or (item.get("grand_total") or {}).get("total_seconds")
            or 0
        )
        if date_str:
            date_to_seconds[date_str] = float(total_seconds)

    return date_to_seconds

def github_like_color(level: int) -> str:
    palette = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]
    return palette[max(0, min(4, level))]

def seconds_to_level(sec: float, max_sec: float) -> int:
    if sec <= 0 or max_sec <= 0:
        return 0
    # log scale for nicer contrast
    x = math.log1p(sec) / math.log1p(max_sec)
    return 1 + int(x * 3.999)  # 1..4

def daterange(start: dt.date, end: dt.date):
    cur = start
    while cur <= end:
        yield cur
        cur += dt.timedelta(days=1)

def build_svg(date_to_seconds, start_date: dt.date, end_date: dt.date, out_path: str, title: str):
    # Align to Sundays for GitHub-like grid (Sun..Sat rows)
    start_sunday = start_date - dt.timedelta(days=(start_date.weekday() + 1) % 7)
    end_saturday = end_date + dt.timedelta(days=(6 - ((end_date.weekday() + 1) % 7)))

    all_dates = list(daterange(start_sunday, end_saturday))
    max_sec = max(date_to_seconds.values(), default=0)

    cell, gap, rx = 11, 2, 2
    weeks, week = [], []
    for d in all_dates:
        week.append(d)
        if len(week) == 7:
            weeks.append(week)
            week = []

    width = len(weeks) * (cell + gap) + 120
    height = 7 * (cell + gap) + 70

    subtitle = f"Daily totals from WakaTime (range: {RANGE})"

    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img" aria-label="{title}">')
    svg.append('<rect width="100%" height="100%" fill="white"/>')
    svg.append(f'<text x="20" y="24" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="16" fill="#111">{title}</text>')
    svg.append(f'<text x="20" y="44" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="12" fill="#666">{subtitle}</text>')

    origin_x, origin_y = 20, 60

    # Minimal weekday labels like GitHub
    labels = [(1, "Mon"), (3, "Wed"), (5, "Fri")]
    for row, text in labels:
        y = origin_y + row * (cell + gap) + cell - 2
        svg.append(f'<text x="{origin_x - 8}" y="{y}" text-anchor="end" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="10" fill="#888">{text}</text>')

    for col, wk in enumerate(weeks):
        for row, d in enumerate(wk):
            x = origin_x + col * (cell + gap)
            y = origin_y + row * (cell + gap)

            date_str = d.isoformat()
            sec = date_to_seconds.get(date_str, 0.0)
            level = seconds_to_level(sec, max_sec)
            color = github_like_color(level)

            if d < start_date or d > end_date:
                color = "#ffffff"

            hours = sec / 3600.0
            tooltip = f"{date_str}: {hours:.2f}h"

            svg.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="{rx}" ry="{rx}" fill="{color}">'
                f'<title>{tooltip}</title>'
                f'</rect>'
            )

    # Legend
    legend_x = origin_x
    legend_y = origin_y + 7 * (cell + gap) + 18
    svg.append(f'<text x="{legend_x}" y="{legend_y}" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="10" fill="#666">Less</text>')
    for i in range(5):
        x = legend_x + 30 + i * (cell + 4)
        svg.append(f'<rect x="{x}" y="{legend_y - 10}" width="{cell}" height="{cell}" rx="{rx}" ry="{rx}" fill="{github_like_color(i)}"/>')
    svg.append(f'<text x="{legend_x + 30 + 5 * (cell + 4) + 6}" y="{legend_y}" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="10" fill="#666">More</text>')

    svg.append("</svg>")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(svg))

def main():
    data = fetch_json_with_retry()
    date_to_seconds = parse_days_insight(data)

    if not date_to_seconds:
        raise RuntimeError("No day data returned. Check API key / scope (read_summaries) and range access.")

    # Determine start/end from returned data if available
    # Fallback: last 365 days for last_year, else last 30 days
    start_iso = data.get("start", "")[:10]
    end_iso = data.get("end", "")[:10]

    if start_iso and end_iso:
        start_date = dt.date.fromisoformat(start_iso)
        end_date = dt.date.fromisoformat(end_iso)
    else:
        today = dt.date.today()
        delta = 365 if RANGE == "last_year" else 30
        end_date = today
        start_date = today - dt.timedelta(days=delta)

    title = f"WakaTime Heatmap ({RANGE})"
    build_svg(date_to_seconds, start_date, end_date, OUT_PATH, title)
    print(f"Generated: {OUT_PATH}")

if __name__ == "__main__":
    main()
