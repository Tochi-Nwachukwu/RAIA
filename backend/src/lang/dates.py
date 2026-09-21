"""Finding dates in text, however an outlet chose to write them."""

import re
from datetime import date

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def date_phrases(iso: str) -> list[str]:
    """Ways a newsroom might write the date `iso` (YYYY-MM-DD)."""
    d = date.fromisoformat(iso)
    month, short = MONTHS[d.month - 1], MONTHS[d.month - 1][:3]
    suffix = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    day = str(d.day)
    return [
        iso,
        f"{day} {month} {d.year}", f"{day}{suffix} {month} {d.year}", f"{day}{suffix} of {month} {d.year}",
        f"{month} {day}, {d.year}", f"{month} {day}{suffix}, {d.year}", f"{month} {day} {d.year}",
        f"{day} {short} {d.year}", f"{short} {day}, {d.year}", f"{short}. {day}, {d.year}",
        f"{d.day:02d}/{d.month:02d}/{d.year}", f"{d.day}/{d.month}/{d.year}",
    ]


def mentions_date(text: str, iso: str) -> bool:
    lowered = re.sub(r"\s+", " ", text.lower())
    return any(p.lower() in lowered for p in date_phrases(iso))
