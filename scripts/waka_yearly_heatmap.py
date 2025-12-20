#!/usr/bin/env python3
import os
import time
import math
import json
import base64
import datetime as dt
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# try:
#     from dotenv import load_dotenv
#     load_dotenv()
# except ImportError:
#     pass  # python-dotenv not installed, skip .env loading


WAKATIME_API_KEY = os.environ["WAKATIME_API_KEY"].strip()
RANGE = os.environ.get("RANGE", "last_year").strip()
URL = f"https://api.wakatime.com/api/v1/users/current/insights/days/{RANGE}"
OUT_PATH = os.environ.get("OUT_PATH", f"assets/waka-heatmap-{RANGE}.svg")

# GitHub-ish palette (light -> dark)
PALETTE = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]

def basic_auth_header(api_key: str) -> str:
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("utf-8")
    return f"Basic {token}"

def fetch_json_with_retry(max_tries=12, sleep_seconds=25):
    """
    For free plan, ranges >= 1 year can be stale on first request.
    Retry until is_up_to_date==True or we run out of tries.
    """
    headers = {
        "Authorization": basic_auth_header(WAKATIME_API_KEY),
        "Accept": "application/json",
        "User-Agent": "waka-heatmap-github-action",
    }

    last_payload = None
    for i in range(1, max_tries + 1):
        try:
            req = Request(URL, headers=headers)
            with urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            last_payload = payload

            data = payload.get("data", {})
            if data.get("is_up_to_date", True):
                return data

            pct = data.get("percent_calculated", 0)
            status = data.get("status", "stale")
            print(f"[retry {i}/{max_tries}] stale: status={status}, percent={pct}%. Sleeping {sleep_seconds}s...")
            time.sleep(sleep_seconds)

        except HTTPError as e:
            if e.code == 402:
                raise RuntimeError(
                    f"HTTP 402 PAYMENT REQUIRED for range '{RANGE}'. "
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

    print("Warning: still stale after retries. Rendering last available payload.")
    if last_payload and "data" in last_payload:
        return last_payload["data"]
    return {}

def parse_days_insight(data):
    """
    Response kamu:
      "date": "YYYY-MM-DD"
      "total": <seconds>   ✅ ini yang dipakai
    """
    days = data.get("days", [])
    date_to_seconds = {}

    for item in days:
        date_str = item.get("date")
        if not date_str:
            if isinstance(item.get("range"), dict) and item["range"].get("start"):
                date_str = item["range"]["start"][:10]

        # ✅ Prioritas: item["total"] (seconds)
        total_seconds = (
            item.get("total")
            or item.get("total_seconds")
            or (item.get("grand_total") or {}).get("total_seconds")
            or 0
        )

        if date_str:
            date_to_seconds[date_str] = float(total_seconds)

    return date_to_seconds

def percentile(sorted_vals, p):
    # p in [0,1]
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = (len(sorted_vals) - 1) * p
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = idx - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)

def make_thresholds(nonzero_seconds):
    """
    GitHub-like intensity: choose thresholds based on distribution.
    Map:
      0 -> level 0
      <= q25 -> 1
      <= q50 -> 2
      <= q75 -> 3
      >  q75 -> 4
    """
    vals = sorted(nonzero_seconds)
    q25 = percentile(vals, 0.25)
    q50 = percentile(vals, 0.50)
    q75 = percentile(vals, 0.75)
    # Ensure monotonic thresholds
    return (q25, q50, q75)

def level_for_seconds(sec, thresholds):
    if sec <= 0:
        return 0
    q25, q50, q75 = thresholds
    if sec <= q25:
        return 1
    if sec <= q50:
        return 2
    if sec <= q75:
        return 3
    return 4

def daterange(start: dt.date, end: dt.date):
    cur = start
    while cur <= end:
        yield cur
        cur += dt.timedelta(days=1)

def build_svg(date_to_seconds, start_date: dt.date, end_date: dt.date, out_path: str):
    # Align to Sunday grid (Sun..Sat rows)
    def dow_sun0(d: dt.date) -> int:
        # Sun=0, Mon=1, ... Sat=6
        return (d.weekday() + 1) % 7

    start_sunday = start_date - dt.timedelta(days=dow_sun0(start_date))
    end_saturday = end_date + dt.timedelta(days=(6 - dow_sun0(end_date)))
    all_dates = list(daterange(start_sunday, end_saturday))

    # Split into weeks (columns)
    weeks = []
    week = []
    for d in all_dates:
        week.append(d)
        if len(week) == 7:
            weeks.append(week)
            week = []

    # thresholds for intensity
    nonzero = [v for v in date_to_seconds.values() if v > 0]
    thresholds = make_thresholds(nonzero)

    # SVG layout
    cell = 11
    gap = 2
    rx = 2

    left_pad = 36  # room for weekday labels
    top_pad = 28   # room for month labels
    title_pad = 24

    width = left_pad + len(weeks) * (cell + gap) + 12
    height = title_pad + top_pad + 7 * (cell + gap) + 34

    title = f"WakaTime Heatmap 2025"
    subtitle = "Daily Coding Time Activity From WakaTime"

    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img" aria-label="{title}">')
    svg.append('<rect width="100%" height="100%" fill="white"/>')

    # Title/subtitle
    svg.append(f'<text x="12" y="18" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="14" fill="#111">{title}</text>')
    svg.append(f'<text x="12" y="34" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="11" fill="#666">{subtitle}</text>')

    origin_x = left_pad
    origin_y = title_pad + top_pad

    # Weekday labels (Mon/Wed/Fri)
    labels = [(1, "Mon"), (3, "Wed"), (5, "Fri")]  # rows where Sun=0
    for row, text in labels:
        y = origin_y + row * (cell + gap) + cell - 2
        svg.append(f'<text x="{left_pad - 6}" y="{y}" text-anchor="end" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="10" fill="#888">{text}</text>')

    # Month labels: place at the week column containing the 1st of each month
    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

    # Find column index for a given date
    # col = number of weeks between start_sunday and that date
    def col_for_date(d: dt.date) -> int:
        delta_days = (d - start_sunday).days
        return max(0, delta_days // 7)

    # Label months that fall within displayed range
    cur = dt.date(start_date.year, start_date.month, 1)
    # Start from first month that might be visible
    if cur < start_date:
        # move to next month
        y = cur.year + (cur.month // 12)
        m = (cur.month % 12) + 1
        cur = dt.date(y, m, 1)

    while cur <= end_date:
        col = col_for_date(cur)
        x = origin_x + col * (cell + gap)
        svg.append(f'<text x="{x}" y="{title_pad + 16}" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="10" fill="#888">{month_names[cur.month-1]}</text>')
        # next month
        y = cur.year + (cur.month // 12)
        m = (cur.month % 12) + 1
        cur = dt.date(y, m, 1)

    # Cells
    for col, wk in enumerate(weeks):
        for row, d in enumerate(wk):
            x = origin_x + col * (cell + gap)
            y = origin_y + row * (cell + gap)

            date_str = d.isoformat()
            sec = float(date_to_seconds.get(date_str, 0.0))

            if d < start_date or d > end_date:
                fill = "#ffffff"
            else:
                lvl = level_for_seconds(sec, thresholds)
                fill = PALETTE[lvl]

            hours = sec / 3600.0
            tooltip = f"{date_str}: {hours:.2f}h"

            svg.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="{rx}" ry="{rx}" fill="{fill}">'
                f'<title>{tooltip}</title>'
                f'</rect>'
            )

    # Legend
    legend_y = origin_y + 7 * (cell + gap) + 18
    legend_x = origin_x + 220  # roughly like GitHub, can be anywhere

    svg.append(f'<text x="{legend_x}" y="{legend_y}" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="10" fill="#666">Less</text>')
    for i in range(5):
        lx = legend_x + 28 + i * (cell + 4)
        svg.append(f'<rect x="{lx}" y="{legend_y - 10}" width="{cell}" height="{cell}" rx="{rx}" ry="{rx}" fill="{PALETTE[i]}"/>')
    svg.append(f'<text x="{legend_x + 28 + 5 * (cell + 4) + 6}" y="{legend_y}" font-family="system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial" font-size="10" fill="#666">More</text>')

    svg.append("</svg>")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(svg))

def main():
    data = fetch_json_with_retry()
    date_to_seconds = parse_days_insight(data)
    nonzero = sum(1 for v in date_to_seconds.values() if v > 0)
    mx = max(date_to_seconds.values(), default=0)
    print("nonzero_days:", nonzero, "max_hours:", mx/3600)

    if not date_to_seconds:
        raise RuntimeError("No day data returned. Parsing failed or API returned empty days.")

    # Prefer API-provided start/end
    start_iso = (data.get("start") or "")[:10]
    end_iso = (data.get("end") or "")[:10]

    if start_iso and end_iso:
        start_date = dt.date.fromisoformat(start_iso)
        end_date = dt.date.fromisoformat(end_iso)
    else:
        # Fallback: last_year ≈ 365 days; else 30
        today = dt.date.today()
        delta = 365 if RANGE == "last_year" else 30
        end_date = today
        start_date = today - dt.timedelta(days=delta)

    build_svg(date_to_seconds, start_date, end_date, OUT_PATH)
    nonzero_days = sum(1 for v in date_to_seconds.values() if v > 0)
    print(f"Generated: {OUT_PATH} | parsed_days={len(date_to_seconds)} | nonzero_days={nonzero_days}")

if __name__ == "__main__":
    main()
