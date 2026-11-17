"""Build the human-readable counts and result tables."""
from __future__ import annotations

from dags.utils.constants import COUNT_COLS


def counts_table(stats: list[dict], tracker_size, dedupe: bool) -> str:
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


def summary_table(jobs: list[dict]) -> str:
    rows = ["| # | Title | Rate | Location | Slug |", "|---|---|---|---|---|"]
    for number, job in enumerate(jobs, 1):
        rows.append(
            f"| {number} | {job['title'][:44]} | {job['rate'][:22]} | "
            f"{job['location'][:20]} | {job['slug']} |"
        )
    return "\n".join(rows)
