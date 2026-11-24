"""Report JD-to-master match percentages as JSON and Markdown."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def load_triage(path: str) -> dict:
    """Load the JSON written by ``batch_triage.py``."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid triage file: {path}")
    return data


def ranked_rows(triage: dict, top_n: int) -> list[dict]:
    """Flatten the top-ranked JDs into match rows."""
    rows: list[dict] = []
    for filename in list(triage.get("ranking") or [])[:top_n]:
        detail = triage.get("jds", {}).get(filename) or {}
        meta = detail.get("meta") or {}
        gaps = detail.get("gaps") or []
        rows.append(
            {
                "jd_file": filename,
                "title": str(meta.get("title") or ""),
                "rate": str(meta.get("rate") or ""),
                "location": str(meta.get("location") or ""),
                "url": str(meta.get("url") or meta.get("permalink") or ""),
                "match": detail.get("match", 0),
                "potential": detail.get("potential", 0),
                "open_questions": len(gaps),
                "missing_terms": [
                    gap.get("term", "") for gap in gaps if isinstance(gap, dict)
                ],
            }
        )
    return rows


def build_summary(match: dict, config: dict, triage: dict) -> dict:
    """Assemble one JSON-serialisable summary for XCom and disk."""
    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "master_file": match["master_file"],
        "source_json": match["source_json"],
        "run_dir": match["run_dir"],
        "jd_dir": match["jd_dir"],
        "jd_count": match["jd_count"],
        "triage_file": match["triage_file"],
        "match_log": match["log_file"],
        "total_ranked": len(triage.get("ranking") or []),
        "top": ranked_rows(triage, int(config["report_top_n"])),
    }


def summary_markdown(summary: dict) -> str:
    """Render the summary as short Markdown."""
    lines = [
        "# JD to master match",
        "",
        f"Generated: {summary['generated']}",
        f"Master: `{summary['master_file']}`",
        f"Source JSON: `{summary['source_json']}`",
        f"JDs matched: {summary['total_ranked']}",
        f"Match log: `{summary['match_log']}`",
        "",
        "| JD | Title | Match | Potential | Open questions |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in summary["top"]:
        lines.append(
            f"| {row['jd_file']} | {row['title']} | {row['match']}% | "
            f"{row['potential']}% | {row['open_questions']} |"
        )
    lines.append("")
    return "\n".join(lines)


def summarize(match: dict, config: dict) -> dict:
    """Write summary.json and summary.md, then log the Markdown table."""
    triage = load_triage(match["triage_file"])
    summary = build_summary(match, config, triage)
    run_dir = Path(match["run_dir"])
    os.makedirs(run_dir, exist_ok=True)
    json_path = str(run_dir / "summary.json")
    md_path = str(run_dir / "summary.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
    markdown = summary_markdown(summary)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(markdown)
    for line in markdown.splitlines():
        _LOGGER.info("%s", line)
    summary["summary_json"] = json_path
    summary["summary_markdown"] = md_path
    return summary
