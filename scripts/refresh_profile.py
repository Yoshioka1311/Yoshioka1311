"""Render the profile cards from public GitHub data using Python's standard library."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser
import json
from math import ceil, pi
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
PALETTE = ("#A78BFA", "#7C3AED", "#0F172A", "#E2E8F0", "#C4B5FD")
VIOLET, PURPLE, NAVY, TEXT, LIGHT = PALETTE
MUTED = "#A8B4CA"
FONT = "system-ui, -apple-system, Segoe UI, Arial, sans-serif"


def fetch(url: str, *, api: bool = False) -> str:
    headers = {"User-Agent": "GitHub-Profile-Stats", "Accept-Language": "en-US"}
    if api:
        headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        token = os.environ.get("PROFILE_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=20) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except URLError:
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError("Unable to fetch GitHub data")


def get_json(path: str):
    return json.loads(fetch(API + path, api=True))


class ContributionCalendar(HTMLParser):
    """Read the dates and visible counts from GitHub's public profile calendar."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.dates: dict[str, date] = {}
        self.counts: dict[str, int] = {}
        self.current_tip: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "td" and "ContributionCalendar-day" in values.get("class", "").split():
            if values.get("id") and values.get("data-date"):
                self.dates[values["id"]] = date.fromisoformat(values["data-date"])
        if tag == "tool-tip" and values.get("for", "").startswith("contribution-day-component-"):
            self.current_tip = values["for"]
            self.parts = []

    def handle_data(self, data):
        if self.current_tip is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "tool-tip" and self.current_tip is not None:
            label = " ".join(" ".join(self.parts).split())
            number = re.match(r"^(No|\d[\d,]*) contributions?\b", label, re.IGNORECASE)
            if number is None:
                raise ValueError("The public GitHub calendar count format changed")
            value = number.group(1)
            self.counts[self.current_tip] = 0 if value.lower() == "no" else int(value.replace(",", ""))
            self.current_tip = None

    def days(self) -> dict[date, int]:
        if not 360 <= len(self.dates) <= 370 or self.dates.keys() != self.counts.keys():
            raise ValueError("GitHub did not return a complete public contribution calendar")
        days = {day: self.counts[key] for key, day in self.dates.items()}
        if len(days) != len(self.dates) or (max(days) - min(days)).days + 1 != len(days):
            raise ValueError("GitHub returned duplicate or missing contribution dates")
        return dict(sorted(days.items()))


def collect(username: str) -> dict:
    login = quote(username, safe="")
    account = get_json(f"/users/{login}")
    if account["login"].lower() != username.lower():
        raise ValueError("GitHub returned a different account")
    repositories = []
    page = 1
    while True:
        batch = get_json(f"/users/{login}/repos?per_page=100&type=owner&page={page}")
        if not isinstance(batch, list):
            raise ValueError("GitHub did not return a repository list")
        repositories.extend(r for r in batch if r["private"] is False and r["owner"]["login"].lower() == username.lower())
        if len(batch) < 100:
            break
        page += 1

    language_bytes: Counter[str] = Counter()
    for repository in repositories:
        if repository["fork"] or repository["name"].lower() == username.lower() or repository["size"] == 0:
            continue
        name = quote(repository["name"], safe="")
        language_bytes.update(get_json(f"/repos/{login}/{name}/languages"))

    calendar = ContributionCalendar()
    calendar.feed(fetch(f"https://github.com/users/{login}/contributions"))
    days = calendar.days()
    end = max(days)
    start = end - timedelta(days=364)
    query = urlencode({"q": f"author:{username} is:public author-date:{start}..{end}", "per_page": 1})
    commits = get_json("/search/commits?" + query)
    if commits["incomplete_results"]:
        raise ValueError("GitHub returned incomplete public commit results")
    monthly: Counter[str] = Counter()
    for day, count in days.items():
        monthly[day.isoformat()[:7]] += count
    return {
        "login": account["login"],
        "calendar_start": min(days).isoformat(),
        "calendar_end": end.isoformat(),
        "public_repositories": len(repositories),
        "stars": sum(r["stargazers_count"] for r in repositories),
        "public_commits": commits["total_count"],
        "contributions": sum(days.values()),
        "language_bytes": dict(language_bytes.most_common()),
        "months": dict(monthly),
    }


def text(x, y, content, *, size=14, color=TEXT, weight=400, anchor="start"):
    return (
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{escape(str(content))}</text>'
    )


def card(width, height, title, description, content):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">\n'
        f'<title id="title">{escape(title)}</title>\n'
        f'<desc id="description">{escape(description)}</desc>\n'
        f'<rect x=".5" y=".5" width="{width-1}" height="{height-1}" rx="17" fill="{NAVY}" stroke="#354364"/>\n'
        + "\n".join(content) + "\n</svg>\n"
    )


def rectangle(x, y, width, height, *, fill="#101A30", stroke="#283650", radius=12):
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>'


def metrics_row(data, width, compact):
    metrics = [
        ("Contributions", data["contributions"], "Last year", 'M2 12H6L9 5L12 16L15 9H20'),
        ("Public commits", data["public_commits"], "Last 365 days", 'M1 10H6M14 10H19M10 6A4 4 0 1 0 10 14A4 4 0 1 0 10 6'),
        ("Public repositories", data["public_repositories"], "Owned repositories", 'M4 3H16V17H4ZM7 3V17M9 7H13M9 10H13'),
        ("Stars earned", data["stars"], "Across public repos", 'M10 2L12.4 7.1L18 7.8L14 11.8L15 17.4L10 14.7L5 17.4L6 11.8L2 7.8L7.6 7.1Z'),
    ]
    columns = 2 if compact else 4
    tile_width = (width - 36 - 10 * (columns - 1)) / columns
    parts = []
    for i, (label, value, scope, icon) in enumerate(metrics):
        x = 18 + (i % columns) * (tile_width + 10)
        y = 76 + (i // columns) * 94
        parts.extend([
            rectangle(x, y, tile_width, 84),
            text(x + 13, y + 21, label, size=12, weight=550, color=LIGHT),
            text(x + 13, y + 55, f"{value:,}", size=29, weight=680),
            text(x + 13, y + 73, scope, size=10.5, color=MUTED),
            f'<rect x="{x+tile_width-39}" y="{y+30}" width="27" height="27" rx="8" fill="#211E3C"/>',
            f'<path d="{icon}" transform="translate({x+tile_width-35.5} {y+33.5})" fill="none" stroke="{VIOLET}" stroke-width="1.35" stroke-linejoin="round" stroke-linecap="round"/>',
        ])
    return parts


def activity_panel(data, x, y, width, height):
    months = list(data["months"].items())
    peak = max(count for _, count in months)
    ceiling = max(4, ceil(peak / 10) * 10)
    left, right = x + 43, x + width - 25
    top, bottom = y + 77, y + height - 34
    step = (right - left) / max(1, len(months) - 1)
    points = [(left + i * step, bottom - (bottom - top) * count / ceiling) for i, (_, count) in enumerate(months)]
    parts = [rectangle(x, y, width, height, fill="#0B1428"),
             text(x+15, y+27, "Contribution activity", size=17, weight=650),
             text(x+15, y+47, "Monthly totals from the public profile", size=11, color=MUTED)]
    for value in (0, ceiling / 2, ceiling):
        grid_y = bottom - (bottom - top) * value / ceiling
        parts.append(f'<path d="M{left} {grid_y}H{right}" stroke="#293752" stroke-width="1" stroke-dasharray="2 4"/>')
        parts.append(text(left-8, grid_y+4, f"{value:g}", size=10, color=MUTED, anchor="end"))
    line = f'M{points[0][0]:.2f} {points[0][1]:.2f}'
    for (px, py), (nx, ny) in zip(points, points[1:]):
        middle = (px + nx) / 2
        line += f' C{middle:.2f} {py:.2f} {middle:.2f} {ny:.2f} {nx:.2f} {ny:.2f}'
    area = f'{line} L{right:.2f} {bottom:.2f} H{left:.2f} Z'
    parts.append(f'<path d="{area}" fill="url(#activity-fill)"/>')
    parts.append(f'<path d="{line}" fill="none" stroke="{VIOLET}" stroke-width="2.3" stroke-linecap="round"/>')
    peak_index = max(range(len(months)), key=lambda i: months[i][1])
    for i, ((month, count), (px, py)) in enumerate(zip(months, points)):
        parts.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="{4 if i == peak_index else 2.4}" fill="{LIGHT}" stroke="#36286A" stroke-width="1"><title>{month}: {count:,}</title></circle>')
        if i in {0, 3, 6, 9, len(months)-1}:
            day = date.fromisoformat(month+'-01')
            label = day.strftime("%b ’%y" if i in {0, len(months)-1} else "%b")
            parts.append(text(round(px,2), bottom+20, label, size=10.5, color=MUTED, anchor="middle"))
    if peak:
        px, py = points[peak_index]
        peak_month = date.fromisoformat(months[peak_index][0]+'-01').strftime('%b')
        badge_x = min(x + width - 88, max(left, px - 35))
        parts.append(rectangle(round(badge_x,2), round(py-28,2), 70, 19, fill="#211C3E", stroke="#544488", radius=5))
        parts.append(text(round(badge_x+35,2), round(py-15,2), f"{peak_month} · {peak:,}", size=10.5, color=LIGHT, anchor="middle"))
    return parts


def language_panel(data, x, y, width, height):
    values = list(data["language_bytes"].items())
    total = sum(size for _, size in values)
    parts = [rectangle(x, y, width, height, fill="#0B1428"),
             text(x+15, y+27, "Language distribution", size=17, weight=650),
             text(x+15, y+47, "Code bytes in public non-fork repos", size=11, color=MUTED)]
    if not total:
        parts.append(text(x+15, y+95, "No language data yet", color=MUTED))
        return parts
    entries = values[:4]
    if len(values) > 4:
        entries.append(("Other", sum(size for _, size in values[4:])))
    colors = (VIOLET, PURPLE, "#7DD3FC", "#646CF2", "#C4B5FD")
    cx, cy, radius = x + 68, y + 133, 40
    circumference = 2 * pi * radius
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="#101A30" stroke="#263652" stroke-width="11"/>')
    offset = 0.0
    for i, (name, size) in enumerate(entries):
        share = 100 * size / total
        share_label = "<0.1%" if 0 < share < .1 else f"{share:.1f}%"
        length = circumference * size / total
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{colors[i]}" stroke-width="11" stroke-dasharray="{length:.4f} {circumference-length:.4f}" stroke-dashoffset="{-offset:.4f}" transform="rotate(-90 {cx} {cy})"><title>{escape(name)}: {size:,} bytes</title></circle>')
        offset += length
        ly = y + 89 + i * 22
        parts.append(f'<circle cx="{x+132}" cy="{ly-4}" r="3.5" fill="{colors[i]}"/>')
        label = name if len(name) <= 11 else name[:10]+"…"
        parts.append(text(x+142, ly, label, size=11.5, weight=550))
        parts.append(text(x+width-15, ly, share_label, size=11.5, color=LIGHT, anchor="end"))
    leading_name, leading_size = entries[0]
    parts.append(text(cx, cy+3, f"{100*leading_size/total:.1f}%", size=19, weight=660, anchor="middle"))
    parts.append(text(cx, cy+19, leading_name[:11], size=10, color=MUTED, anchor="middle"))
    return parts


def dashboard(data, compact=False):
    width, height = (380, 752) if compact else (780, 441)
    refreshed = date.fromisoformat(data["calendar_end"]).strftime("%d %b %Y")
    parts = [
        f'<defs><linearGradient id="activity-fill" x2="0" y2="1"><stop stop-color="{PURPLE}" stop-opacity=".48"/><stop offset="1" stop-color="{PURPLE}" stop-opacity=".04"/></linearGradient></defs>',
        rectangle(18, 20, 27, 27, fill="#242042", stroke="#4C3E78", radius=8),
        '<path d="M25 34V40M31.5 28V40M38 31V40" stroke="#C4B5FD" stroke-width="2.3" stroke-linecap="round"/>',
        text(55, 34, "GitHub dashboard", size=20, weight=680),
        text(55, 52, "@"+data["login"], size=11, color=MUTED),
        rectangle(width-136, 25, 118, 23, fill="#151D33", stroke="#35425F", radius=10),
        f'<circle cx="{width-125}" cy="36.5" r="3" fill="{VIOLET}"/>',
        text(width-74, 40, refreshed, size=10.5, color=LIGHT, anchor="middle"),
        f'<path d="M18 63H{width-18}" stroke="#2B3854"/>',
    ]
    parts += metrics_row(data, width, compact)
    if compact:
        parts += activity_panel(data, 18, 266, width-36, 219)
        parts += language_panel(data, 18, 495, width-36, 219)
    else:
        parts += activity_panel(data, 18, 173, 449, 230)
        parts += language_panel(data, 477, 173, 285, 230)
    parts += [text(18, height-15, "GitHub public data", size=10, color=MUTED),
              text(width-18, height-15, "Daily refresh", size=10, color=MUTED, anchor="end")]
    description = f'{data["contributions"]:,} profile-visible contributions, {data["public_commits"]:,} indexed public commits, {data["public_repositories"]:,} owned public repositories, and {data["stars"]:,} stars earned. Through {refreshed}.'
    return card(width, height, "GitHub dashboard for " + data["login"], description, parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--snapshot", type=Path, help="Optional local file for review data")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", args.username):
        parser.error("Invalid GitHub username")
    data = collect(args.username)
    images = {
        "dashboard.svg": dashboard(data),
        "dashboard-compact.svg": dashboard(data, compact=True),
    }
    for svg in images.values():
        ElementTree.fromstring(svg)
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    for name, svg in images.items():
        temporary = assets / (name + ".tmp")
        temporary.write_text(svg, encoding="utf-8", newline="\n")
        temporary.replace(assets / name)
    if args.snapshot:
        args.snapshot.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f'Updated two dashboard layouts for {data["login"]} through {data["calendar_end"]}')


if __name__ == "__main__":
    main()
