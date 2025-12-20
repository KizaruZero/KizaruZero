#!/usr/bin/env python3
import os
import time
import math
import base64
import datetime as dt
from urllib.request import Request, urlopen
import json

WAKATIME_API_KEY = os.environ["WAKATIME_API_KEY"].strip()
YEAR = int(os.environ.get("YEAR", dt.datetime.utcnow().year))
OUT_PATH = os.environ.get("OUT_PATH", f"assets/waka-heatmap-{YEAR}.svg")

# Insight endpoint (days) for yearly range
URL = f"https://api.wakatime.com/api/v1/users/current/insights/days/{YEAR}"

def basic_auth_header(api_key: str) -> str:
    # WakaTime: Basic base64(API_KEY:)
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("utf-8")
    return f"Basic {token}"

def fetch_json_with_retry(max_tries=10, sleep_seconds=20):
    """
    Free-plan note: yearly ranges can be stale on first request.
    We retry until is_up_to_date == True or we run out of tries.
    """
    headers = {
        "Authorization": basic_auth_header(WAKATIME_API_KEY),
        "Accept": "application/json",
        "User-Agent": "waka-yearly-heatmap-github-action"
    }

    last = None
    for i in range(1, max_tries + 1):
        req = Request(URL, headers=headers)
        with urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        data = payload.get("data", {})
        last = data
        if data.get("is_up_to_date", True):
            return data

        pct = data.get("percent_calculated", 0)
        status = data.get("status", "stale")
        print(f"[retry {i}/{max_tries}] stale: status={status}, percent={pct}%. Sleeping {sleep_seconds}s...")
        time.sleep(sleep_seconds)

    # If still stale, return the last response (we'll still try to render what we have)
    print("Warning: insights still stale after retries. Rendering last available data.")
    return last or {}

def parse_days_insight(data):
    """
    Expecting: data["days"] as list of day objects.
    We handle a couple common shapes.
    Output: dict date->total_seconds
    """
    days = data.get("days", [])
    date_to_seconds = {}

    for item in days:
        # Common fields seen in WakaTime responses
        date_str = item.get("date") or item.get("range", {}).get("date")
        total_seconds = (
            item.get("total_seconds")
            or item.get("grand_total", {}).get("total_seconds")
            or 0
        )
        if not date_str:
            continue
        date_to_seconds[date_str] = float(total_seconds)

    return date_to_seconds

def github_like_color(level: int) -> str:
    """
    Simple 5-level palette (GitHub-ish). You can tweak later.
    level: 0..4
    """
    palette = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]
    return palette[max(0, min(4, level))]

def seconds_to_level(sec: float, max_sec: float) -> int:
    if sec <= 0 or max_sec <= 0:
        return 0
    # Use log scaling so huge days don't flatten everything
    x = math.log1p(sec) / math.log1p(max_sec)
    # Map to 1..4 (0 reserved for zero)
    return 1 + int(x * 3.999)

def daterange(start: dt.date, end: dt.date):
    cur = start
    while cur <= end:
        yield cur
        cur += dt.timedelta(days=1)

def build_svg(date_to_seconds, year: int, out_path: str):
    # Define year date range
    start = dt.date(year, 1, 1)
    end = dt.date(year, 12, 31)

    # GitHub heatmap layout: weeks as columns, weekdays as rows
    # We'll align weeks starting on Sunday (like GitHub contributions graph)
    # Find the Sunday on/before Jan 1
    start_sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7)  # weekday: Mon=0..Sun=6
    # Convert to list of all dates covering full weeks
    end_saturday = end + dt.timedelta(days=(6 - ((end.weekday() + 1) % 7)))
    all_dates = list(daterange(start_sunday, end_saturday))

    # Compute max seconds for scaling
    max_sec = max(date_to_seconds.values(), default=0)

    # SVG cell sizes
    cell = 11
    gap = 2
    rx = 2

    weeks = []
    week = []
    for d in all_dates:
        week.append(d)
        if len(week) == 7:
            weeks.append(week)
            week = []

    width = len(weeks) * (cell + gap) + 120
    height = 7 * (cell + gap) + 60

    title = f"WakaTime {year} – Time Spent Heatmap"
    subtitle = "Daily totals (seconds) from WakaTime insights/days"

    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img" aria-label="{title}">')
    svg.append('<rect width="100%" height="100%" fill="white"/>')
    svg.append(f'<text x="20" y="24" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="16" fill="#111">{title}</text>')
    svg.append(f'<text x="20" y="44" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="12" fill="#666">{subtitle}</text>')

    origin_x = 20
    origin_y = 60

    # Draw weekday labels (Mon, Wed, Fri) like GitHub minimal labels
    labels = [(1, "Mon"), (3, "Wed"), (5, "Fri")]
    for row, text in labels:
        y = origin_y + row * (cell + gap) + cell - 2
        svg.append(f'<text x="{origin_x - 8}" y="{y}" text-anchor="end" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="10" fill="#888">{text}</text>')

    # Draw cells
    for col, week in enumerate(weeks):
        for row, d in enumerate(week):
            x = origin_x + col * (cell + gap)
            y = origin_y + row * (cell + gap)

            date_str = d.isoformat()
            sec = date_to_seconds.get(date_str, 0.0)
            level = seconds_to_level(sec, max_sec)
            color = github_like_color(level)

            # Grey out dates outside the year range
            if d < start or d > end:
                color = "#ffffff"

            # Tooltip
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
    data = fetch_json_with_retry(max_tries=12, sleep_seconds=25)
    date_to_seconds = parse_days_insight(data)

    if not date_to_seconds:
        print("No day data found in insights response. Check API key scope (read_summaries) and endpoint access.")
    build_svg(date_to_seconds, YEAR, OUT_PATH)
    print(f"Generated: {OUT_PATH}")

if __name__ == "__main__":
    main()
