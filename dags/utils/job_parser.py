"""Turn JobServe detail/listing HTML into a normalised job record.

Also owns job identity (slug/hash/uid), the rendered JD text and the small
rate/IR35 helpers used by the tracker writer.
"""
from __future__ import annotations

import hashlib
import re

from dags.utils.constants import BASE, DETAIL_IDS, RATE_RE, UNIT_TO_DAY, UNIT_TO_YEAR
from dags.utils.html import extract


def slugify(title: str, code: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].strip("-")
    return f"{slug}-{code.lower()}" if slug else code.lower()


def content_hash(job: dict) -> str:
    basis = "|".join(
        " ".join((job.get(key) or "").lower().split())
        for key in ("title", "agency", "location", "description")
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def uid_of(job: dict) -> str:
    permalink = (job.get("permalink") or "").strip().rstrip("/")
    if permalink:
        segment = permalink.split("/")[-1]
        if segment and "." not in segment and segment.lower() not in (
            "jobserve.com", "www.jobserve.com",
        ):
            return "permalink:" + segment
    if job.get("code"):
        return "code:" + job["code"]
    return "hash:" + content_hash(job)


def build_jd_text(job: dict) -> str:
    parts = [job["title"]]
    head = " - ".join(value for value in (job.get("location"), job.get("rate")) if value)
    if head:
        parts.append(head)
    if job.get("ir35"):
        parts.append(f"IR35: {job['ir35']}")
    when = " | ".join(
        value
        for value in (
            f"Type: {job['employment_type']}" if job.get("employment_type") else "",
            f"Duration: {job['duration']}" if job.get("duration") else "",
            f"Start: {job['start_date']}" if job.get("start_date") else "",
        )
        if value
    )
    if when:
        parts.append(when)
    if job.get("agency"):
        parts.append(f"Posted by: {job['agency']}")
    if job.get("posted_date"):
        parts.append(f"Posted: {job['posted_date']}")
    parts.append("")
    parts.append(job.get("description") or "(no description)")
    if job.get("skills"):
        parts += ["", "Skills:"] + [f"- {skill}" for skill in job["skills"]]
    meta = [
        f"{label}: {job[key]}"
        for label, key in (
            ("Location", "location"), ("Industry", "industry"),
            ("Duration", "duration"), ("Start Date", "start_date"),
            ("Rate", "rate"), ("Employment Business", "agency"),
            ("Contact", "contact"), ("Reference", "reference"),
            ("Posted Date", "posted_date"), ("Permalink", "permalink"),
        )
        if job.get(key)
    ]
    if meta:
        parts += [""] + meta
    return "\n".join(parts)


def _field(text: dict, key: str) -> str:
    return (text.get(key) or "").strip()


def ir35_status(text: str) -> str:
    lowered = (text or "").lower()
    if re.search(r"inside\s+ir35|ir35\s*[:=]\s*inside\b", lowered):
        return "Inside"
    if re.search(r"outside\s+ir35|ir35\s*[:=]\s*outside\b", lowered):
        return "Outside"
    if re.search(r"\bumbrella\b|\bpaye\b|\bfcsa\b", lowered):
        return "Inside"
    if re.search(r"\bpermanent\b", lowered):
        return "Permanent"
    return ""


def _fmt_number(value: float) -> str:
    rounded = round(value, 2)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def rates(rate_text: str, permanent: bool = False) -> tuple[str, str]:
    """Return ``(min, max)`` normalised to day rate, or to year for permanent."""
    table = UNIT_TO_YEAR if permanent else UNIT_TO_DAY
    values = []
    for match in RATE_RE.finditer(str(rate_text or "")):
        amount = float(match.group("amount").replace(",", ""))
        if match.group("thousand"):
            amount *= 1000
        scale = table.get(match.group("unit").lower())
        if scale:
            values.append(amount * scale)
    if not values:
        return "", ""
    return _fmt_number(min(values)), _fmt_number(max(values))


def agent(text: str) -> str:
    match = re.search(
        r"(?:posted\s+by|contact|employment\s+business)\s*:\s*(.+)",
        text or "", re.I,
    )
    return match.group(1).strip() if match else ""


def parse_job(html: str, code: str) -> dict:
    text, attrs = extract(html, DETAIL_IDS)
    title = _field(text, "td_jobpositionnolink") or _field(text, "md_ref") or code
    skills = [
        line[2:].strip()
        for line in _field(text, "md_skills").splitlines()
        if line.startswith("- ") and line[2:].strip()
    ]
    description = "\n".join(
        line
        for line in _field(text, "md_skills").splitlines()
        if line.strip()
        and not line.startswith("- ")
        and line.strip().rstrip(":") != "Skills"
    )
    job = {
        "slug": slugify(title, code),
        "code": code,
        "url": f"{BASE}/gb/en/{code}.jsjob",
        "permalink": attrs.get("md_permalink", {}).get("href", "").strip(),
        "title": title,
        "rate": _field(text, "md_rate"),
        "location": _field(text, "md_location"),
        "ir35": "",
        "employment_type": _field(text, "td_job_type"),
        "industry": _field(text, "md_industry"),
        "duration": _field(text, "md_duration"),
        "start_date": _field(text, "md_start_date"),
        "agency": _field(text, "md_recruiter"),
        "contact": _field(text, "md_contact"),
        "reference": _field(text, "md_ref"),
        "posted_date": _field(text, "md_posted_date"),
        "skills": skills,
        "description": description,
    }
    job["hash"] = content_hash(job)
    job["uid"] = uid_of(job)
    status = ir35_status(build_jd_text(job))
    job["ir35_status"] = status
    job["ir35"] = status.lower() if status in ("Inside", "Outside") else ""
    job["jd_text"] = build_jd_text(job)
    return job


def listing_title(html: str, code: str) -> str:
    """Extract the search-row title for a code, or an empty string."""
    match = re.search(
        r"/gb/en/" + re.escape(code) + r'\.jsjob"[^>]*title="([^"]+?)\s+job\b[^"]*"',
        html, re.I,
    )
    return match.group(1).strip() if match else ""


def fallback_job(code: str, title: str = "") -> dict:
    """Build the fallback record used when a detail page cannot be fetched."""
    title = (title or "").strip() or code
    job = {
        "slug": slugify(title, code),
        "code": code,
        "url": f"{BASE}/gb/en/{code}.jsjob",
        "permalink": "",
        "title": title,
        "rate": "",
        "location": "",
        "ir35": "",
        "agency": "",
        "employment_type": "",
        "contact": "",
        "reference": "",
        "posted_date": "",
        "industry": "",
        "duration": "",
        "start_date": "",
        "skills": [],
        "description": "",
        "error": "detail page not fetched",
    }
    job["hash"] = content_hash(job)
    job["uid"] = uid_of(job)
    job["jd_text"] = build_jd_text(job)
    return job


def parse_listing_meta(html: str, code: str) -> dict:
    """Fallback when a detail page cannot be fetched: fields from the search row."""
    return fallback_job(code, listing_title(html, code))
