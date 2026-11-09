"""Build the profile dashboard from GitHub's public data."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
GREEN = ("#25292D", "#244735", "#2D7B4D", "#47AF6C", "#80E6A0")
LANGUAGE_COLORS = ("#B8B1A4", "#7C9EAA", "#B79AAD", "#A0A97F", "#8A8299")


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
    return json.loads(fetch("https://api.github.com" + path, api=True))


def repository_summary(username: str, end: date) -> dict:
    login = quote(username, safe="")
    repositories = []
    page = 1
    while True:
        batch = get_json(f"/users/{login}/repos?type=owner&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("GitHub did not return a repository list")
        repositories.extend(repo for repo in batch if not repo["private"] and repo["owner"]["login"].lower() == username.lower())
        if len(batch) < 100:
            break
        page += 1

    language_bytes: Counter[str] = Counter()
    for repo in repositories:
        if repo["fork"] or repo["name"].lower() == username.lower() or not repo["size"]:
            continue
        name = quote(repo["name"], safe="")
        language_bytes.update(get_json(f"/repos/{login}/{name}/languages"))

    start = end - timedelta(days=364)
    query = urlencode({"q": f"author:{username} is:public author-date:{start}..{end}", "per_page": 1})
    commits = get_json("/search/commits?" + query)
    if commits["incomplete_results"]:
        raise ValueError("GitHub returned incomplete public commit results")

    return {
        "repositories": len(repositories),
        "stars": sum(repo["stargazers_count"] for repo in repositories),
        "commits": commits["total_count"],
        "languages": [
            {"name": name, "bytes": size, "color": LANGUAGE_COLORS[index % len(LANGUAGE_COLORS)]}
            for index, (name, size) in enumerate(language_bytes.most_common()) if size > 0
        ],
    }


class ContributionCalendar(HTMLParser):
    """Read the dates and visible counts from GitHub's public profile calendar."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.dates: dict[str, date] = {}
        self.counts: dict[str, int] = {}
        self.levels: dict[str, int] = {}
        self.current_tip: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "td" and "ContributionCalendar-day" in values.get("class", "").split():
            if values.get("id") and values.get("data-date"):
                key = values["id"]
                self.dates[key] = date.fromisoformat(values["data-date"])
                level = int(values["data-level"])
                if not 0 <= level < len(GREEN):
                    raise ValueError("GitHub returned an unknown contribution level")
                self.levels[key] = level
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

    def days(self) -> list[tuple[date, int, int]]:
        if not 360 <= len(self.dates) <= 370 or self.dates.keys() != self.counts.keys():
            raise ValueError("GitHub did not return a complete public contribution calendar")
        days = {day: self.counts[key] for key, day in self.dates.items()}
        if len(days) != len(self.dates) or (max(days) - min(days)).days + 1 != len(days):
            raise ValueError("GitHub returned duplicate or missing contribution dates")
        return sorted((day, self.counts[key], self.levels[key]) for key, day in self.dates.items())


def week_start(day: date) -> date:
    return day - timedelta(days=(day.weekday() + 1) % 7)


def render_dashboard(days: list[tuple[date, int, int]], summary: dict, *, compact: bool = False) -> str:
    width, calendar_height, pitch, size, inset = (380, 164, 13, 10, 16) if compact else (760, 179, 13.5, 10.5, 24)
    height = calendar_height + 148
    first = week_start(days[0][0])
    if compact:
        first = max(first, week_start(days[-1][0]) - timedelta(weeks=26))
        days = [entry for entry in days if entry[0] >= first]
    period = "Recent 27 weeks" if compact else "Past year"
    count = sum(entry[1] for entry in days)
    count_label = f"{count:,} contribution{'s' if count != 1 else ''}"
    start_label = days[0][0].strftime("%b %d, %Y").replace(" 0", " ")
    end_label = days[-1][0].strftime("%b %d, %Y").replace(" 0", " ")
    parts = [f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">GitHub activity dashboard</title>
  <desc id="desc">{count_label} between {start_label} and {end_label}. Brighter green marks higher activity. {summary["repositories"]:,} public repositories, {summary["stars"]:,} stars earned, and {summary["commits"]:,} indexed public commits over the past year.</desc>
  <style>
    text {{ font-family: system-ui, sans-serif; }}
    .active {{ animation: sweep 700ms cubic-bezier(.22,1,.36,1) var(--delay) both; }}
    @keyframes sweep {{ 0%,100% {{ opacity: 1; }} 34% {{ fill: #B5FFD0; }} }}
    @media (prefers-reduced-motion: reduce) {{ .active {{ animation: none; }} }}
  </style>
  <rect x=".5" y=".5" width="{width-1}" height="{height-1}" rx="12" fill="#14171B" stroke="#373C43"/>
  <text x="{inset}" y="30" fill="#F2F2F1" font-size="15" font-weight="650">{period}</text>
  <text x="{width-inset}" y="30" text-anchor="end" fill="#C9CDD0" font-size="13">{count_label}</text>"""]

    seen_months = set()
    last_label_x = -100
    for day, _, level in days:
        week, weekday = divmod((day - first).days, 7)
        x = inset + week * pitch
        y = 56 + weekday * pitch
        if day.month not in seen_months:
            seen_months.add(day.month)
            if x - last_label_x >= 34 and x + 24 <= width - 7:
                parts.append(f'  <text x="{x:g}" y="48" font-size="10.5" fill="#C9CDD0">{day:%b}</text>')
                last_label_x = x
        motion = f' class="active" style="--delay:{week * 24}ms"' if level else ""
        parts.append(f'  <rect x="{x:g}" y="{y:g}" width="{size}" height="{size}" rx="2" fill="{GREEN[level]}"{motion}/>')

    legend_x = width - inset - 93
    footer_y = calendar_height - 12
    parts.append(f'  <text x="{legend_x}" y="{footer_y}" font-size="10.5" fill="#C9CDD0">Less</text>')
    for level, color in enumerate(GREEN):
        parts.append(f'  <rect x="{legend_x+25+level*9}" y="{footer_y-7}" width="7" height="7" rx="1.5" fill="{color}"/>')
    parts.append(f'  <text x="{width-inset}" y="{footer_y}" text-anchor="end" font-size="10.5" fill="#C9CDD0">More</text>')
    if not compact:
        parts.append(f'  <text x="{inset}" y="{footer_y}" font-size="10.5" fill="#C9CDD0">{start_label} to {end_label}</text>')

    content_width = width - 2 * inset
    parts.append(f'  <path d="M{inset} {calendar_height}h{content_width}" stroke="#373C43"/>')
    metrics = (("Public repos", "repositories"), ("Stars earned", "stars"), ("Public commits", "commits"))
    for index, (label, key) in enumerate(metrics):
        x = inset + content_width * index / 3
        parts.append(f'  <text x="{x:g}" y="{calendar_height+26}" font-size="18" font-weight="650" fill="#F2F2F1">{summary[key]:,}</text>')
        parts.append(f'  <text x="{x:g}" y="{calendar_height+43}" font-size="11.5" fill="#C9CDD0">{label}</text>')

    languages = summary["languages"]
    total = sum(language["bytes"] for language in languages)
    parts.append(f'  <path d="M{inset} {calendar_height+60}h{content_width}" stroke="#373C43"/>')
    parts.append(f'  <text x="{inset}" y="{calendar_height+83}" font-size="13" font-weight="650" fill="#F2F2F1">Repository languages</text>')
    parts.append(f'  <text x="{width-inset}" y="{calendar_height+83}" text-anchor="end" font-size="11" fill="#C9CDD0">Share of code bytes</text>')
    if total:
        offset = inset
        for language in languages:
            length = content_width * language["bytes"] / total
            parts.append(f'  <rect x="{offset:.4f}" y="{calendar_height+95}" width="{length:.4f}" height="7" fill="{language["color"]}"/>')
            offset += length
        for index, language in enumerate(languages[:3]):
            x = inset + content_width * index / 3
            percentage = 100 * language["bytes"] / total
            share = "&lt;0.1%" if percentage < 0.1 else f"{percentage:.1f}%"
            name = escape(language["name"] if len(language["name"]) <= 12 or not compact else language["name"][:11] + "…")
            parts.append(f'  <circle cx="{x+3:g}" cy="{calendar_height+123}" r="3" fill="{language["color"]}"/>')
            parts.append(f'  <text x="{x+11:g}" y="{calendar_height+127}" font-size="11.5" fill="#C9CDD0">{name} {share}</text>')
    else:
        parts.append(f'  <text x="{inset}" y="{calendar_height+111}" font-size="11.5" fill="#C9CDD0">No language data for public repositories.</text>')

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", args.username):
        parser.error("Invalid GitHub username")

    calendar = ContributionCalendar()
    login = quote(args.username, safe="")
    calendar.feed(fetch(f"https://github.com/users/{login}/contributions"))
    days = calendar.days()
    summary = repository_summary(args.username, days[-1][0])

    annual = render_dashboard(days, summary)
    compact = render_dashboard(days, summary, compact=True)
    for svg in (annual, compact):
        ElementTree.fromstring(svg)

    data = json.dumps({
        "login": args.username,
        "days": [(day.isoformat(), count, level) for day, count, level in days],
        "colors": GREEN,
        "summary": summary,
    }, separators=(",", ":")).replace("<", "\\u003c")
    template = (ROOT / "scripts/activity.html").read_text(encoding="utf-8")
    if template.count("{{ACTIVITY_DATA}}") != 1:
        raise ValueError("The activity page needs one data placeholder")
    outputs = {
        ROOT / "assets/activity.svg": annual,
        ROOT / "assets/activity-compact.svg": compact,
        ROOT / "assets/activity.json": data + "\n",
        ROOT / "docs/index.html": template.replace("{{ACTIVITY_DATA}}", data),
    }
    for path, value in outputs.items():
        path.parent.mkdir(exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(value, encoding="utf-8", newline="\n")
        temporary.replace(path)
    print(f"Updated GitHub activity through {days[-1][0]}")


if __name__ == "__main__":
    main()
