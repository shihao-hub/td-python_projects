"""Render the self-contained HTML demo by injecting payload JSON into the template."""

from __future__ import annotations

from importlib import resources

PLACEHOLDER = "__DATA_PLACEHOLDER__"


def render_demo(payload_json: str) -> str:
    tpl = (resources.files("zedhub") / "templates" / "demo.html").read_text(encoding="utf-8")
    if PLACEHOLDER not in tpl:
        raise RuntimeError("demo.html is missing the data placeholder")
    # avoid accidental </script> breakout inside the inline JSON
    safe = payload_json.replace("</", "<\\/")
    return tpl.replace(PLACEHOLDER, safe)
