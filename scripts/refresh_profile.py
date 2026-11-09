"""Render the profile contribution calendar from GitHub's public activity data."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
GREEN = ("#25292D", "#244735", "#2D7B4D", "#47AF6C", "#80E6A0")


def fetch(url: str) -> str:
    headers = {"User-Agent": "GitHub-Profile-Stats", "Accept-Language": "en-US"}
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


def render_calendar(days: list[tuple[date, int, int]], *, compact: bool = False) -> str:
    width, height, pitch, size, inset = (380, 164, 13, 10, 16) if compact else (760, 179, 13.5, 10.5, 24)
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
  <title id="title">{period} of GitHub activity</title>
  <desc id="desc">{count_label} between {start_label} and {end_label}. Brighter green marks higher activity.</desc>
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
    footer_y = height - 12
    parts.append(f'  <text x="{legend_x}" y="{footer_y}" font-size="10.5" fill="#C9CDD0">Less</text>')
    for level, color in enumerate(GREEN):
        parts.append(f'  <rect x="{legend_x+25+level*9}" y="{footer_y-7}" width="7" height="7" rx="1.5" fill="{color}"/>')
    parts.append(f'  <text x="{width-inset}" y="{footer_y}" text-anchor="end" font-size="10.5" fill="#C9CDD0">More</text>')
    if not compact:
        parts.append(f'  <text x="{inset}" y="{footer_y}" font-size="10.5" fill="#C9CDD0">{start_label} to {end_label}</text>')
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

    annual = render_calendar(days)
    compact = render_calendar(days, compact=True)
    for svg in (annual, compact):
        ElementTree.fromstring(svg)

    data = json.dumps({
        "login": args.username,
        "days": [(day.isoformat(), count, level) for day, count, level in days],
        "colors": GREEN,
    }, separators=(",", ":"))
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
