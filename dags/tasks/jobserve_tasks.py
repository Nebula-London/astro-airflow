"""Airflow 3 TaskFlow tasks for the ``jobserve-new-jobs`` skill.

This is the full find-jobs pipeline: load a config, fetch every saved search,
parse the job details, de-duplicate against the state file / prior extracts /
tracker, log the counts table, then write the dated JSON and tracker rows.

Two rules are applied throughout this module:

* every helper is defined at module level -- there are no functions inside
  functions (and no lambdas);
* there are no ``print`` calls -- all output goes through ``_LOGGER``.

Search URLs contain a private JobServe ``shid``. They are read from the
Airflow Variable ``jobserve_search_urls`` (or the ``JOBSERVE_SEARCH_URLS``
environment variable) and are never put in config, XCom or log messages.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import partial
from html.parser import HTMLParser
from pathlib import Path

import yaml
from airflow.sdk import Variable, task

_LOGGER = logging.getLogger(__name__)

BASE = "https://www.jobserve.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) "
    "Gecko/20100101 Firefox/115.0"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.5",
    "Accept-Encoding": "identity",
}
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3",
    "h4", "h5", "h6", "section", "article",
}
CODE = re.compile(r"(?:/gb/en/)?([0-9A-Z]{16,})\.jsjob")
DETAIL_IDS = [
    "td_jobpositionnolink", "td_job_type", "md_rate", "md_location",
    "md_industry", "md_duration", "md_start_date", "md_recruiter",
    "md_contact", "md_ref", "md_posted_date", "md_permalink", "md_skills",
    "md_email",
]
COUNT_COLS = ("found", "dup", "tracker", "processed", "unfetched", "new")
SEARCH_URLS_VARIABLE = "jobserve_search_urls"
TRACKER_COLUMNS = [
    "code", "url", "title", "location", "rate", "rate_min", "rate_max",
    "ir35", "agent", "agency", "first_seen", "jd_file", "fit",
    "missing_terms", "cv_pdf", "cv_built_on", "status", "applied_on",
    "rate_submitted", "consultancy", "client", "notes", "reason",
]
RATE_RE = re.compile(
    r"(?P<amount>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<thousand>k)?\s*"
    r"(?:/|per\s+|an?\s+)?\s*"
    r"(?P<unit>hour|hours|hr|hrs|ph|day|days|pd|week|weeks|pw|month|months|pm|year|years|pa|annum)\b",
    re.I,
)
UNIT_TO_DAY = {
    "hour": 7.5, "hours": 7.5, "hr": 7.5, "hrs": 7.5, "ph": 7.5,
    "day": 1.0, "days": 1.0, "pd": 1.0,
    "week": 0.2, "weeks": 0.2, "pw": 0.2,
    "month": 12.0 / 220.0, "months": 12.0 / 220.0, "pm": 12.0 / 220.0,
    "year": 1.0 / 220.0, "years": 1.0 / 220.0, "pa": 1.0 / 220.0,
    "annum": 1.0 / 220.0,
}
UNIT_TO_YEAR = {
    "hour": 7.5 * 220, "hours": 7.5 * 220, "hr": 7.5 * 220,
    "hrs": 7.5 * 220, "ph": 7.5 * 220,
    "day": 220.0, "days": 220.0, "pd": 220.0,
    "week": 52.0, "weeks": 52.0, "pw": 52.0,
    "month": 12.0, "months": 12.0, "pm": 12.0,
    "year": 1.0, "years": 1.0, "pa": 1.0, "annum": 1.0,
}


# --------------------------------------------------------------- configuration
def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    return loaded if isinstance(loaded, dict) else {}


def _read_search_urls() -> dict[str, str]:
    """Read {search name: URL} from the Airflow Variable or the env fallback."""
    raw = ""
    try:
        raw = Variable.get(SEARCH_URLS_VARIABLE, default="")
    except Exception as exc:  # noqa: BLE001 - local/dev fallback
        _LOGGER.info(
            "Airflow Variable %s is unavailable (%s); using the environment fallback",
            SEARCH_URLS_VARIABLE, exc,
        )
    if not raw:
        raw = os.environ.get("JOBSERVE_SEARCH_URLS", "")
    if not raw:
        raise ValueError(
            "No JobServe searches configured. Set the Airflow Variable "
            f"'{SEARCH_URLS_VARIABLE}' (or the environment variable "
            "JOBSERVE_SEARCH_URLS) to a JSON object of {name: search URL}. "
            "Never commit a search URL or shid."
        )
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict) or not data:
        raise ValueError(
            f"'{SEARCH_URLS_VARIABLE}' must be a non-empty JSON object of name -> URL"
        )
    cleaned = {}
    for name, url in data.items():
        label, value = str(name).strip(), str(url or "").strip()
        if label and value:
            cleaned[label] = value
    if not cleaned:
        raise ValueError(f"'{SEARCH_URLS_VARIABLE}' contained no usable name/URL pairs")
    return cleaned


@task
def load_jobserve_config(config_path: str) -> dict:
    """Read the non-secret config and resolve all output paths.

    Only search *names* are returned; the shid-bearing URLs stay in the
    secret store and are re-read by the task that fetches the listings.
    """
    raw = _load_yaml(config_path)
    urls = _read_search_urls()
    names = []
    for entry in raw.get("searches") or []:
        name = str(entry.get("name") if isinstance(entry, dict) else entry).strip()
        if name and name not in names:
            names.append(name)
    if not names:
        names = list(urls)
    missing = [name for name in names if name not in urls]
    if missing:
        raise ValueError(
            f"{len(missing)} configured search name(s) have no saved URL: "
            f"{', '.join(missing)}. Update Airflow Variable '{SEARCH_URLS_VARIABLE}'."
        )

    output_dir = str(
        Path(str(raw.get("output_dir") or _project_root() / "include" / "jobserve")).expanduser()
    )
    output_path = Path(output_dir)
    config = {
        "searches": [{"name": name} for name in names],
        "output_dir": output_dir,
        "state_file": str(Path(str(raw.get("state_file") or output_path / "state.json")).expanduser()),
        "seen_dir": output_dir,
        "tracker_file": str(
            Path(str(raw.get("tracker_file") or output_path / "applications.csv")).expanduser()
        ),
        "jd_dir": str(Path(str(raw.get("jd_dir") or output_path / "jds")).expanduser()),
        "workers": max(1, int(raw.get("workers", 6))),
        "delay_seconds": max(0.0, float(raw.get("delay_seconds", 0.0))),
        "timeout_seconds": float(raw.get("timeout_seconds", 30.0)),
        "http_retries": max(1, int(raw.get("http_retries", 3))),
        "limit": max(0, int(raw.get("limit", 0))),
        "dedupe": bool(raw.get("dedupe", True)),
        "use_tracker": bool(raw.get("use_tracker", True)),
        "status_style": str(raw.get("status_style") or ""),
    }
    _LOGGER.info(
        "Loaded JobServe config: %d search(es), output_dir=%s, dedupe=%s",
        len(names), config["output_dir"], config["dedupe"],
    )
    return config


# ------------------------------------------------------------------------- http
def fetch(url: str, timeout: float = 30.0, retries: int = 3) -> tuple[str, str]:
    """GET a URL with a browser UA, retrying transport errors with backoff."""
    last_error = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace"), response.geturl()
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            last_error = exc
            if attempt < retries - 1:
                _LOGGER.info(
                    "Retrying a JobServe request after a transport error (%d/%d)",
                    attempt + 1, retries,
                )
                time.sleep(1.5 ** attempt)
    raise RuntimeError(f"request failed after {retries} attempt(s): {last_error}")


# --------------------------------------------------------------- html -> text
def _clean(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


class _Extract(HTMLParser):
    """Collect text and attributes of elements whose id is in ``ids``."""

    def __init__(self, ids):
        super().__init__(convert_charrefs=True)
        self.ids = set(ids)
        self.stack = []
        self.caps = []
        self.text = {}
        self.attrs = {}

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            if tag == "br" and self.caps:
                self.caps[-1]["parts"].append("\n")
            return
        self.stack.append(tag)
        if tag in BLOCK and self.caps:
            self.caps[-1]["parts"].append("\n")
        if tag == "li" and self.caps:
            self.caps[-1]["parts"].append("- ")
        attributes = dict(attrs)
        tid = attributes.get("id")
        if tid in self.ids and tid not in self.text:
            self.caps.append({"id": tid, "depth": len(self.stack), "parts": []})
            self.attrs[tid] = attributes

    def handle_endtag(self, tag):
        if (
            self.caps
            and self.caps[-1]["depth"] == len(self.stack)
            and self.stack
            and self.stack[-1] == tag
        ):
            cap = self.caps.pop()
            self.text[cap["id"]] = _clean("".join(cap["parts"]))
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        for cap in self.caps:
            cap["parts"].append(data)


def extract(html: str, ids) -> tuple[dict, dict]:
    parser = _Extract(ids)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup: keep what we have
        _LOGGER.info("Malformed JobServe markup; keeping the fields parsed so far")
    return parser.text, parser.attrs


def find_job_codes(html: str, final_url: str, src_url: str = "") -> list[str]:
    """Job codes from a listing, a detail page, or a permalink redirect.

    A search id (shid) is never a job code, so an empty listing must not turn
    its own shid into a role.
    """
    if ".jsjob" in final_url:
        return list(dict.fromkeys(CODE.findall(final_url)))
    codes = list(dict.fromkeys(CODE.findall(html)))
    if codes:
        return codes
    shids = {
        value.upper()
        for value in re.findall(r"shid=([0-9A-Za-z]+)", f"{src_url} {final_url} {html}", re.I)
    }
    for pattern in (r"jid=([0-9A-Z]{16,})", r"([0-9A-Z]{16,})/?$"):
        matches = [
            code
            for code in (re.findall(pattern, html) or re.findall(pattern, final_url))
            if code.upper() not in shids
        ]
        if matches:
            return list(dict.fromkeys(matches))
    return []


# ----------------------------------------------------------------- job parsing
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


def _ir35_status(text: str) -> str:
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


def _rates(rate_text: str, permanent: bool = False) -> tuple[str, str]:
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


def _agent(text: str) -> str:
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
    status = _ir35_status(build_jd_text(job))
    job["ir35_status"] = status
    job["ir35"] = status.lower() if status in ("Inside", "Outside") else ""
    job["jd_text"] = build_jd_text(job)
    return job


def parse_listing_meta(html: str, code: str) -> dict:
    """Fallback when a detail page cannot be fetched: fields from the search row."""
    match = re.search(
        r"/gb/en/" + re.escape(code) + r'\.jsjob"[^>]*title="[^"]*? ([^"]+?)\s+job"',
        html, re.I,
    )
    title = (match.group(1).strip() if match else "") or code
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


# ------------------------------------------------------------- fetch pipeline
def _fetch_search(url: str, config: dict) -> tuple[list[str], str]:
    """Fetch a listing and every results page, returning codes and page HTML."""
    html, final_url = fetch(url, config["timeout_seconds"], config["http_retries"])
    codes = find_job_codes(html, final_url, url)
    pages = sorted({int(number) for number in re.findall(r"[?&;]page=(\d+)", html)})
    max_page = max(pages) if pages else 0
    known = set(codes)
    if max_page > 1:
        _LOGGER.info("Listing has %d page(s)", max_page)
        for page in range(2, max_page + 1):
            separator = "&" if "?" in url else "?"
            try:
                page_html, page_final = fetch(
                    f"{url}{separator}page={page}",
                    config["timeout_seconds"], config["http_retries"],
                )
            except Exception:  # noqa: BLE001 - one bad page must not stop the search
                _LOGGER.warning("Page %d/%d not fetched; retrying on the next run", page, max_page)
                continue
            new_codes = [
                code for code in find_job_codes(page_html, page_final, url)
                if code not in known
            ]
            known.update(new_codes)
            codes.extend(new_codes)
            html += page_html
            _LOGGER.info("Page %d/%d: %d new row(s)", page, max_page, len(new_codes))
    if config["limit"]:
        codes = codes[: config["limit"]]
    if not codes:
        _LOGGER.warning("A saved search returned no roles (empty results or an invalid URL)")
    else:
        _LOGGER.info("Listing returned %d role(s)", len(codes))
    return codes, html


@task
def fetch_search_codes(config: dict) -> dict:
    """Fetch each saved search (URLs come from the secret store, not XCom)."""
    urls = _read_search_urls()
    searches = []
    for search in config["searches"]:
        name = search["name"]
        codes, html = _fetch_search(urls[name], config)
        searches.append({"name": name, "codes": codes, "html": html})
        _LOGGER.info("Search '%s': %d listing row(s)", name, len(codes))
    return {"searches": searches}


def _fetch_job_detail(code: str, listing_html: str, config: dict) -> dict:
    for attempt in range(config["http_retries"]):
        try:
            html, _ = fetch(
                f"{BASE}/gb/en/{code}.jsjob", config["timeout_seconds"], 1,
            )
            return parse_job(html, code)
        except Exception:  # noqa: BLE001 - fall back to the listing row
            if attempt < config["http_retries"] - 1:
                time.sleep(1.5 ** attempt)
    return parse_listing_meta(listing_html, code)


@task
def parse_job_details(fetched: dict, config: dict) -> dict:
    """Follow every job code to its detail page, de-duplicating across searches."""
    jobs = []
    ids = set()
    stats = []
    for search in fetched["searches"]:
        name = search["name"]
        codes = search["codes"]
        counts = dict.fromkeys(COUNT_COLS, 0)
        counts["found"] = len(codes)
        detail_fetcher = partial(
            _fetch_job_detail, listing_html=search["html"], config=config,
        )
        with ThreadPoolExecutor(max_workers=config["workers"]) as pool:
            found = list(pool.map(detail_fetcher, codes))
        for job in found:
            key = job.get("uid") or ("hash:" + job.get("hash", ""))
            if key in ids:
                counts["dup"] += 1
                continue
            ids.add(key)
            job["search_name"] = name
            job["search"] = name  # never carry a shid-bearing URL into XCom
            jobs.append(job)
        stats.append({"name": name, "counts": counts})
        if config["delay_seconds"]:
            time.sleep(config["delay_seconds"])
        _LOGGER.info("Search '%s': parsed %d job detail(s)", name, len(found))
    return {"jobs": jobs, "stats": stats}


# --------------------------------------------------------------------- de-dupe
def load_state(path: str) -> dict:
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (ValueError, OSError):
            _LOGGER.info("State file unreadable; starting with an empty state")
    return {"version": 1, "seen": {}}


def seen_ids(state: dict, seen_dir: str) -> set[str]:
    """Seen roles from the state file plus any prior dated extract."""
    seen = set()
    for uid, record in (state.get("seen") or {}).items():
        seen.add(uid)
        if isinstance(record, dict) and record.get("hash"):
            seen.add("hash:" + record["hash"])
    if seen_dir and os.path.isdir(seen_dir):
        for name in os.listdir(seen_dir):
            if not name.endswith("_jd-extracted-from-jobserve.json"):
                continue
            try:
                with open(os.path.join(seen_dir, name), encoding="utf-8") as handle:
                    data = json.load(handle)
            except (ValueError, OSError):
                continue
            jobs = data.get("jobs", []) if isinstance(data, dict) else (
                data if isinstance(data, list) else []
            )
            for job in jobs:
                if isinstance(job, dict):
                    if job.get("uid"):
                        seen.add(job["uid"])
                    if job.get("hash"):
                        seen.add("hash:" + job["hash"])
    return seen


def is_seen(job: dict, seen: set[str]) -> bool:
    return job.get("uid") in seen or ("hash:" + job.get("hash", "")) in seen


def save_state(path: str, jobs: list[dict]) -> None:
    state = load_state(path)
    seen = state.setdefault("seen", {})
    for job in jobs:
        seen[job["uid"]] = {
            "slug": job["slug"],
            "code": job.get("code", ""),
            "hash": job.get("hash", ""),
            "permalink": job.get("permalink", ""),
            "first_seen": date.today().isoformat(),
        }
    state["version"] = state.get("version", 1)
    state["updated"] = date.today().isoformat()
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)


@task
def deduplicate_jobs(parsed: dict, config: dict) -> dict:
    """Drop roles already in the tracker, state file or an earlier extract."""
    jobs = parsed["jobs"]
    stats = parsed["stats"]
    by_name = {row["name"]: row["counts"] for row in stats}
    tracked = _tracker_codes(config["tracker_file"]) if config["use_tracker"] else set()
    prior = seen_ids(load_state(config["state_file"]), config["seen_dir"]) if config["dedupe"] else set()
    tracked_ids = {"permalink:" + code for code in tracked} | {
        "code:" + code for code in tracked
    }
    seen = prior | tracked_ids
    new = []
    unfetched = []
    skipped = 0
    for job in jobs:
        counts = by_name.setdefault(job.get("search_name", ""), dict.fromkeys(COUNT_COLS, 0))
        if config["dedupe"] and is_seen(job, tracked_ids):
            counts["tracker"] += 1
            skipped += 1
            continue
        if config["dedupe"] and is_seen(job, prior):
            counts["processed"] += 1
            skipped += 1
            continue
        if job.get("error"):
            counts["unfetched"] += 1
            unfetched.append(job.get("code", ""))
            skipped += 1
            continue
        counts["new"] += 1
        new.append(job)
    _LOGGER.info(
        "De-duplication: %d new, %d skipped, %d not fetched, %d role(s) in tracker",
        len(new), skipped, len(unfetched), len(tracked),
    )
    return {
        "new": new,
        "unfetched": unfetched,
        "stats": stats,
        "skipped": skipped,
        "tracker_size": len(tracked) if config["use_tracker"] else None,
        "dedupe": config["dedupe"],
    }


# --------------------------------------------------------------- counts report
def _counts_table(stats: list[dict], tracker_size, dedupe: bool) -> str:
    rows = [
        "COUNTS (this run)",
        "| Search | Found | Duplicate (other search) | Already in tracker | "
        "Already processed | Not fetched | New today |",
        "|---|---|---|---|---|---|---|",
    ]
    total = dict.fromkeys(COUNT_COLS, 0)
    for row in stats:
        counts = row["counts"]
        rows.append(
            f"| {row['name']} | "
            + " | ".join(str(counts[column]) for column in COUNT_COLS)
            + " |"
        )
        for column in COUNT_COLS:
            total[column] += counts[column]
    rows.append(
        "| **Total** | "
        + " | ".join(f"**{total[column]}**" for column in COUNT_COLS)
        + " |"
    )
    notes = [f"unique roles: {total['found'] - total['dup']}"]
    notes.append(
        f"tracker: {tracker_size} role(s) on the sheet/CSV"
        if tracker_size is not None else "tracker: not used"
    )
    if not dedupe:
        notes.append("de-dup off: every role counts as new")
    rows.append("; ".join(notes))
    rows.append(
        "Already processed = in state.json or an earlier dated extract but not in "
        "the tracker. Not fetched = detail page failed; retried next run."
    )
    return "\n".join(rows)


@task
def report_counts(result: dict) -> dict:
    """Log the counts table first, as soon as de-duplication is done."""
    for line in _counts_table(result["stats"], result["tracker_size"], result["dedupe"]).splitlines():
        _LOGGER.info("%s", line)
    return result


# ---------------------------------------------------------------------- output
def dated_name(day: date | None = None) -> str:
    return (day or date.today()).isoformat() + "_jd-extracted-from-jobserve.json"


def _write_json(path: str, config: dict, jobs: list[dict], skipped: int) -> dict:
    existing = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            existing = data.get("jobs", []) if isinstance(data, dict) else (
                data if isinstance(data, list) else []
            )
        except (ValueError, OSError):
            existing = []
    by_uid = {}
    for job in existing + jobs:
        by_uid.setdefault(job.get("uid") or job.get("slug"), job)
    merged = list(by_uid.values())
    data = {
        "source": ", ".join(search["name"] for search in config["searches"]),
        "generated": date.today().isoformat(),
        "count": len(merged),
        "skipped": skipped,
        "jobs": merged,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    _LOGGER.info("Wrote %d job(s) to the dated JSON", len(merged))
    return data


def _write_jd_dir(dirpath: str, jobs: list[dict]) -> None:
    os.makedirs(dirpath, exist_ok=True)
    for job in jobs:
        with open(os.path.join(dirpath, job["slug"] + ".txt"), "w", encoding="utf-8") as handle:
            handle.write(build_jd_text(job).rstrip() + "\n")
    _LOGGER.info("Wrote %d JD text file(s)", len(jobs))


def _normalize_code(value) -> str:
    return re.sub(r"^\s*\d+\s*[-.]\s*", "", str(value or "")).strip()


def _tracker_codes(path: str) -> set[str]:
    codes = set()
    if not path or not os.path.exists(path):
        return codes
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            code = _normalize_code(row.get("code"))
            if code:
                codes.add(code)
    return codes


def _tracker_fields(job: dict) -> dict:
    text = job.get("jd_text") or build_jd_text(job)
    status = job.get("ir35_status") or _ir35_status(text)
    low, high = _rates(job.get("rate", ""), permanent=(status == "Permanent"))
    return {
        "ir35": status,
        "rate_min": low,
        "rate_max": high,
        "agent": _agent(text),
    }


def track_code(job: dict) -> str:
    uid = job.get("uid") or ""
    return uid.split(":", 1)[1] if uid.startswith(("permalink:", "code:")) else job.get("code", "")


def track_row(job: dict, jd_dir: str = "", status_style: str = "") -> dict:
    status = "1-new" if status_style == "numbered" else "new"
    return {
        "code": track_code(job),
        "url": job.get("permalink") or job.get("url", ""),
        "title": job.get("title", ""),
        "location": job.get("location", ""),
        "rate": job.get("rate", ""),
        "status": status,
        "first_seen": date.today().isoformat(),
        "jd_file": os.path.join(jd_dir, job["slug"] + ".txt") if jd_dir else "",
        **_tracker_fields(job),
    }


def _upsert_tracker(path: str, rows: list[dict], status_style: str = "") -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    columns = list(TRACKER_COLUMNS)
    existing = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames:
                columns = list(reader.fieldnames)
                for column in TRACKER_COLUMNS:
                    if column not in columns:
                        columns.append(column)
            existing = list(reader)
    known = {_normalize_code(row.get("code")) for row in existing}
    added = 0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in existing:
            writer.writerow(row)
        for row in rows:
            if _normalize_code(row.get("code")) in known:
                continue
            writer.writerow(row)
            added += 1
    _LOGGER.info("Tracker updated: %d new row(s) appended", added)


def _summary(jobs: list[dict]) -> str:
    rows = ["| # | Title | Rate | Location | Slug |", "|---|---|---|---|---|"]
    for number, job in enumerate(jobs, 1):
        rows.append(
            f"| {number} | {job['title'][:44]} | {job['rate'][:22]} | "
            f"{job['location'][:20]} | {job['slug']} |"
        )
    return "\n".join(rows)


@task
def write_results(result: dict, config: dict) -> str:
    """Write the dated JSON, JD text, state file and tracker CSV."""
    jobs = result["new"]
    out_path = str(Path(config["output_dir"]) / dated_name())
    _write_json(out_path, config, jobs, result["skipped"])
    if config["jd_dir"] and jobs:
        _write_jd_dir(config["jd_dir"], jobs)
    if config["dedupe"] and jobs:
        save_state(config["state_file"], jobs)
    if config["use_tracker"] and jobs:
        rows = [
            track_row(job, config["jd_dir"], config["status_style"])
            for job in jobs
        ]
        _upsert_tracker(config["tracker_file"], rows, config["status_style"])
    for line in _summary(jobs).splitlines():
        _LOGGER.info("%s", line)
    total = sum(row["counts"]["found"] for row in result["stats"])
    already_tracked = sum(row["counts"]["tracker"] for row in result["stats"])
    _LOGGER.info(
        "RESULT new=%d seen=%d already_tracked=%d total=%d unfetched=%d json=%s",
        len(jobs), result["skipped"], already_tracked, total,
        len(result["unfetched"]), out_path,
    )
    return out_path
