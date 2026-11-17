"""HTML helpers: capture elements by id and find job codes in a page."""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

from dags.utils.constants import BLOCK, CODE, VOID

_LOGGER = logging.getLogger(__name__)


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
    """Return ``(text, attrs)`` captured for the requested element ids."""
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
