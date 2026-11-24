"""Offline end-to-end tests for the mapped fetch/parse/dedupe flow.

These tests need Airflow installed (they import the secret helper module), so
run them inside the Astro dev container.
"""
from __future__ import annotations

from dags.utils import fetching

JOBSERVE_LISTING = (
    '<a href="/gb/en/ABCDEF1234567890.jsjob" '
    'title="AWS Engineer job in Manchester">AWS Engineer</a>'
)
JOBSERVE_DETAIL = """
<div id="td_jobpositionnolink">AWS Engineer</div>
<div id="md_rate">£600 per day</div>
<div id="md_location">Manchester</div>
<div id="md_recruiter">Acme</div>
<div id="md_ref">REF1</div>
<div id="md_skills">Build cloud systems</div>
"""
TOTALJOBS_LISTING = (
    '<a href="/job/aws-engineer/acme-job108217598">AWS Engineer</a>'
    '<div>Acme</div><div>Manchester</div><p>Build cloud systems</p>'
)
TOTALJOBS_DETAIL = """
<script type="application/ld+json">
{"@type":"JobPosting","title":"AWS Engineer","description":"Build cloud systems",
 "hiringOrganization":{"name":"Acme"},
 "jobLocation":{"address":{"addressLocality":"Manchester","addressCountry":"GB"}},
 "url":"https://www.totaljobs.com/job/aws-engineer/acme-job108217598",
 "identifier":{"value":"108217598"}}
</script>
"""


def _fake_fetch(url, timeout=30.0, retries=3):
    if "jobserve" in url:
        body = JOBSERVE_DETAIL if ".jsjob" in url else JOBSERVE_LISTING
    else:
        body = TOTALJOBS_DETAIL if "/job/" in url and "jobs/" not in url else TOTALJOBS_LISTING
    return body, url


def _config(tmp_path):
    return {
        "workers": 2,
        "delay_seconds": 0,
        "timeout_seconds": 5,
        "http_retries": 1,
        "limit": 0,
        "dedupe": True,
        "use_tracker": True,
        "state_file": str(tmp_path / "state.json"),
        "seen_dir": str(tmp_path),
        "tracker_file": str(tmp_path / "applications.csv"),
    }


def test_mapped_fetch_parse_and_cross_portal_merge(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fetching,
        "read_portal_urls",
        lambda: {
            "jobserve": "https://www.jobserve.com/gb/en/JobListing.aspx?shid=ABCDEF1234567890",
            "totaljobs": "https://www.totaljobs.com/jobs/{query}",
        },
    )
    monkeypatch.setattr(fetching, "fetch", _fake_fetch)

    config = _config(tmp_path)
    targets = [
        {"key": "jobserve::aws", "portal": "jobserve", "role": "aws"},
        {"key": "totaljobs::aws", "portal": "totaljobs", "role": "aws"},
    ]
    fetched = [fetching.fetch_target(target, config) for target in targets]
    assert all("url" not in row for row in fetched)  # no secret URL in XCom
    assert [len(row["items"]) for row in fetched] == [1, 1]

    parsed = [fetching.parse_target(row, config) for row in fetched]
    assert [len(row["jobs"]) for row in parsed] == [1, 1]

    from dags.utils.dedupe import deduplicate

    result = deduplicate(parsed, config)
    assert len(result["new"]) == 1  # one canonical row for two portals
    job = result["new"][0]
    assert {source["portal"] for source in job["sources"]} == {"jobserve", "totaljobs"}
    assert job["uid"].startswith("hash:")

    from dags.utils.tracker import track_row, upsert_tracker

    upsert_tracker(config["tracker_file"], [track_row(job, "", "")])
    import csv

    with open(config["tracker_file"], newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert "jobserve:" in rows[0]["sources"] and "totaljobs:" in rows[0]["sources"]


def test_fetch_target_prefers_role_specific_url_override(monkeypatch, tmp_path):
    seen = []

    def _capture(url, timeout=30.0, retries=3):
        seen.append(url)
        return _fake_fetch(url, timeout, retries)

    monkeypatch.setattr(
        fetching,
        "read_portal_urls",
        lambda: {
            "jobserve": "https://www.jobserve.com/gb/en/JobListing.aspx?shid=GENERIC",
            "jobserve::aws": "https://www.jobserve.com/gb/en/JobListing.aspx?shid=AWS123",
            "totaljobs": "https://www.totaljobs.com/jobs/{query}",
        },
    )
    monkeypatch.setattr(fetching, "fetch", _capture)

    target = {"key": "jobserve::aws", "portal": "jobserve", "role": "aws"}
    fetching.fetch_target(target, _config(tmp_path))

    assert seen == ["https://www.jobserve.com/gb/en/JobListing.aspx?shid=AWS123"]
