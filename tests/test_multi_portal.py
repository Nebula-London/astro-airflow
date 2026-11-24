"""Unit tests for the reusable multi-portal adapter and dedupe layer.

These tests use small inline HTML samples rather than live sites so they can
run offline. No Airflow import is required.
"""
from __future__ import annotations

import csv
import json

from dags.portals.base import build_search_url, merge_sources, normalize_job
from dags.portals.jobserve import find_items as jobserve_items
from dags.portals.totaljobs import find_items as totaljobs_items
from dags.portals.totaljobs import parse_detail as totaljobs_detail
from dags.utils.config import build_targets
from dags.utils.dedupe import is_seen, save_state, seen_ids
from dags.utils.identity import canonical_uid, job_keys, source_key
from dags.utils.tracker import tracker_seen_keys, upsert_tracker

JOBSERVE_LISTING = """
<html><body>
<a href="/gb/en/ABCDEF1234567890.jsjob" title="Senior AWS Engineer job in Manchester">Senior AWS Engineer</a>
<a href="/gb/en/ZZZZZZ0987654321.jsjob" title="Data Engineer job in London">Data Engineer</a>
</body></html>
"""

TOTALJOBS_LISTING = """
<html><body>
<a href="/job/aws-cloud-engineer/anson-mccade-job108217598">AWS Cloud Engineer</a>
<div>Anson McCade</div><div>Manchester</div><div>£70,000 per annum</div><p>Work with AWS</p>
<a href="/job/data-engineer/acme-job108217599">Data Engineer</a>
<div>Acme</div><div>London</div><div>£65,000 per annum</div><p>Build pipelines</p>
</body></html>
"""

TOTALJOBS_DETAIL = """
<html><body>
<script type="application/ld+json">
{"@type":"JobPosting","title":"AWS Cloud Engineer","description":"<p>Work with AWS</p>",
 "datePosted":"2026-11-17","hiringOrganization":{"name":"Anson McCade"},
 "jobLocation":{"address":{"addressLocality":"Manchester","addressRegion":"Greater Manchester","addressCountry":"GB"}},
 "employmentType":"FULL_TIME",
 "url":"https://www.totaljobs.com/job/aws-cloud-engineer/anson-mccade-job108217598",
 "identifier":{"value":"108217598"}}
</script>
</body></html>
"""


def _normalized(title, agency, location, description, portal, code):
    job = {
        "title": title,
        "agency": agency,
        "location": location,
        "description": description,
    }
    item = {
        "code": code,
        "url": f"https://example.com/job/{code}",
        "title": title,
    }
    return normalize_job(job, item, portal, "aws")


def test_search_url_template_encodes_role_by_position():
    assert (
        build_search_url("https://www.totaljobs.com/jobs/{query}/in-manchester", "data engineer")
        == "https://www.totaljobs.com/jobs/data-engineer/in-manchester"
    )
    assert (
        build_search_url("https://uk.indeed.com/jobs?q={query}", "data engineer")
        == "https://uk.indeed.com/jobs?q=data+engineer"
    )
    # JobServe keeps its fixed shid URL: no query param, so nothing is injected.
    assert (
        build_search_url("https://www.jobserve.com/gb/en/JobListing.aspx?shid=ABC123", "aws", "")
        == "https://www.jobserve.com/gb/en/JobListing.aspx?shid=ABC123"
    )


def test_jobserve_adapter_finds_codes_and_titles():
    items = jobserve_items(JOBSERVE_LISTING, "https://www.jobserve.com/gb/en/Results.aspx")
    assert [item["code"] for item in items] == ["ABCDEF1234567890", "ZZZZZZ0987654321"]
    assert items[0]["title"] == "Senior AWS Engineer"
    assert items[0]["url"].endswith("ABCDEF1234567890.jsjob")


def test_totaljobs_adapter_finds_cards_and_parses_jsonld():
    items = totaljobs_items(TOTALJOBS_LISTING, "https://www.totaljobs.com/jobs/aws/in-manchester")
    assert [item["code"] for item in items] == ["108217598", "108217599"]
    assert items[0]["agency"] == "Anson McCade"
    assert items[0]["location"] == "Manchester"

    job = totaljobs_detail(TOTALJOBS_DETAIL, "108217598", items[0], "totaljobs", "aws")
    assert job["title"] == "AWS Cloud Engineer"
    assert job["agency"] == "Anson McCade"
    assert job["portal"] == "totaljobs"
    assert job["source_code"] == "108217598"
    assert job["uid"].startswith("hash:")


def test_canonical_identity_merges_sources_across_portals():
    first = _normalized("AWS Engineer", "Acme", "Manchester", "Build cloud systems", "jobserve", "ABC123")
    second = _normalized("AWS Engineer", "Acme", "Manchester", "Build cloud systems", "totaljobs", "999")
    assert canonical_uid(first) == canonical_uid(second)
    merged = merge_sources([first, second])
    assert {source["portal"] for source in merged["sources"]} == {"jobserve", "totaljobs"}
    assert source_key("totaljobs", "999") in job_keys(merged)


def test_build_targets_fans_out_and_honours_exclusions():
    raw = {
        "searches": [
            {"name": "aws"},
            {"name": "data engineer", "portal": "cw"},
            {"name": "platform engineer", "exclude_portals": ["totaljobs"]},
        ],
        "portals": {
            "jobserve": {"enabled": True},
            "totaljobs": {"enabled": True},
            "cw": {"enabled": True},
            "indeed": {"enabled": False},
        },
    }
    urls = {
        "jobserve": "https://www.jobserve.com/{query}",
        "totaljobs": "https://www.totaljobs.com/jobs/{query}",
        "cw": "https://www.cwjobs.co.uk/jobs/{query}",
    }
    targets, _ = build_targets(raw, urls)
    keys = [target["key"] for target in targets]
    assert "jobserve::aws" in keys
    assert "totaljobs::aws" in keys
    assert "cw::aws" in keys
    assert "cw::data engineer" in keys
    assert "totaljobs::platform engineer" not in keys
    assert not any(key.startswith("indeed::") for key in keys)


def test_state_and_tracker_remember_every_source(tmp_path):
    job = _normalized("AWS Engineer", "Acme", "Manchester", "Build", "totaljobs", "999")
    state_path = tmp_path / "state.json"
    legacy = {
        "version": 2,
        "seen": {
            "code:OLDCODE": {"code": "OLDCODE", "hash": "oldhash"},
        },
    }
    state_path.write_text(json.dumps(legacy), encoding="utf-8")
    from dags.utils.dedupe import load_state

    state = load_state(str(state_path))
    assert "code:OLDCODE" in seen_ids(state, str(tmp_path))
    assert is_seen(job, {source_key("totaljobs", "999")})

    # Adding a second portal source to the same canonical job must not create a
    # second tracker row, and must preserve an existing status.
    tracker_path = tmp_path / "applications.csv"
    row = {
        "uid": canonical_uid(job),
        "canonical_hash": job["hash"],
        "portal": "jobserve",
        "source_code": "ABC123",
        "sources": source_key("jobserve", "ABC123"),
        "code": "ABC123",
        "status": "applied",
        "title": job["title"],
    }
    tracker_path.write_text("", encoding="utf-8")
    with tracker_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)

    updated = dict(row)
    updated["portal"] = "totaljobs"
    updated["source_code"] = "999"
    updated["sources"] = source_key("totaljobs", "999")
    updated["status"] = "new"
    upsert_tracker(str(tracker_path), [updated])

    with tracker_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["status"] == "applied"
    assert source_key("jobserve", "ABC123") in rows[0]["sources"]
    assert source_key("totaljobs", "999") in rows[0]["sources"]
    assert tracker_seen_keys(str(tracker_path)) >= {canonical_uid(job), source_key("totaljobs", "999")}


def test_save_state_merges_new_sources(tmp_path):
    first = _normalized("AWS Engineer", "Acme", "Manchester", "Build", "jobserve", "ABC123")
    second = _normalized("AWS Engineer", "Acme", "Manchester", "Build", "totaljobs", "999")
    path = tmp_path / "nested" / "state.json"
    save_state(str(path), [first])
    save_state(str(path), [second])
    record = json.loads(path.read_text(encoding="utf-8"))["seen"][canonical_uid(second)]
    assert {source["portal"] for source in record["sources"]} == {"jobserve", "totaljobs"}


def test_legacy_role_urls_merge_with_portal_templates(monkeypatch):
    from dags.connections import portal_secrets

    values = {
        portal_secrets.PORTAL_URLS_VARIABLE: json.dumps(
            {"totaljobs": "https://www.totaljobs.com/jobs/{query}"}
        ),
        portal_secrets.LEGACY_SEARCH_URLS_VARIABLE: json.dumps(
            {
                "aws": "https://www.jobserve.com/gb/en/JobListing.aspx?shid=AWS",
                "data engineer": "https://www.jobserve.com/gb/en/JobListing.aspx?shid=DE",
            }
        ),
    }
    monkeypatch.setattr(
        portal_secrets, "_raw_value", lambda variable, env_name: values.get(variable, "")
    )

    urls = portal_secrets.read_portal_urls()

    assert urls["totaljobs"] == "https://www.totaljobs.com/jobs/{query}"
    assert urls["jobserve::aws"].endswith("shid=AWS")
    assert urls["jobserve::data engineer"].endswith("shid=DE")
    assert urls["jobserve"].endswith("shid=AWS")


def test_per_portal_role_url_variables(monkeypatch):
    from dags.connections import portal_secrets

    values = {
        "totaljobs_search_urls": json.dumps(
            {
                "aws": "https://www.totaljobs.com/jobs/aws",
                "data engineer": "https://www.totaljobs.com/jobs/data-engineer",
            }
        ),
        "cw_search_urls": json.dumps({"aws": "https://www.cwjobs.co.uk/jobs/aws"}),
    }
    monkeypatch.setattr(
        portal_secrets, "_raw_value", lambda variable, env_name: values.get(variable, "")
    )

    urls = portal_secrets.read_portal_urls()

    assert urls["totaljobs::aws"] == "https://www.totaljobs.com/jobs/aws"
    assert urls["totaljobs::data engineer"] == "https://www.totaljobs.com/jobs/data-engineer"
    assert urls["cw::aws"] == "https://www.cwjobs.co.uk/jobs/aws"
    assert urls["totaljobs"] == urls["totaljobs::aws"]
    assert urls["cw"] == urls["cw::aws"]
