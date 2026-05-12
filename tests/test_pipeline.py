"""
Unit + smoke tests for pptx_pipeline.

Run with:  python -m pytest tests/ -v
Or:        python tests/test_pipeline.py

The deterministic tests don't require an Anthropic API key. Smoke tests that
hit the LLM are guarded by ANTHROPIC_API_KEY and `--smoke` (set RUN_SMOKE=1).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest

import pptx_pipeline as pipeline
from pptx_pipeline import (
    DeckPlan,
    SlideSpec,
    Theme,
    _placeholder_body,
    _render_one_slide,
    _sanitize_body,
    _validate_body,
    render_slides_parallel,
    run_node_script,
    stitch_script,
)


def _fake_plan(n: int) -> DeckPlan:
    theme = Theme(
        palette={
            "primary": "1E2761",
            "secondary": "CADCFC",
            "accent": "F9C846",
            "background": "FFFFFF",
            "text": "222222",
        },
        fonts={"header": "Georgia", "body": "Calibri"},
        layout_size="LAYOUT_16x9",
        motif="left accent bar",
    )
    slides = [
        SlideSpec(
            index=i,
            layout="content",
            title=f"Slide {i}",
            bullets=[f"Point {j}" for j in range(1, 4)],
        )
        for i in range(1, n + 1)
    ]
    return DeckPlan(deck_title="Test Deck", theme=theme, slides=slides)


def _trivial_body(i: int) -> str:
    return f"""  const slide = pres.addSlide();
  slide.background = {{ color: "FFFFFF" }};
  slide.addText("Hello {i}", {{ x: 0.5, y: 0.5, w: 9, h: 1, fontSize: 28, bold: true, color: "1E2761" }});
  slide.addShape(pres.shapes.RECTANGLE, {{ x: 0.5, y: 1.6, w: 0.1, h: 3, fill: {{ color: "F9C846" }}, line: {{ type: "none" }} }});"""


# ── Stitcher ──────────────────────────────────────────────────────────────────

def test_stitch_includes_all_slides():
    plan = _fake_plan(3)
    fragments = [_trivial_body(i) for i in range(1, 4)]
    script = stitch_script(plan, fragments, Path("/tmp/test_out.pptx"))

    for i in range(1, 4):
        assert f"async function addSlide_{i}(pres)" in script
        assert f"await addSlide_{i}(pres);" in script
    assert 'pres.layout = "LAYOUT_16x9"' in script
    assert '"Test Deck"' in script
    assert 'require("pptxgenjs")' in script


def test_stitch_is_syntactically_valid_node():
    plan = _fake_plan(5)
    fragments = [_trivial_body(i) for i in range(1, 6)]
    out = Path("/tmp/test_stitch_syntax.pptx")
    script = stitch_script(plan, fragments, out)

    script_path = REPO_ROOT / "_test_syntax_check.js"
    script_path.write_text(script)
    try:
        result = subprocess.run(
            ["node", "--check", str(script_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Node syntax check failed:\n{result.stderr}"
    finally:
        script_path.unlink(missing_ok=True)


@pytest.mark.skipif(not (REPO_ROOT / "node_modules" / "pptxgenjs").exists(), reason="pptxgenjs not installed")
def test_stitch_runs_and_writes_pptx():
    plan = _fake_plan(3)
    fragments = [_trivial_body(i) for i in range(1, 4)]
    out = REPO_ROOT / "_test_smoke_output.pptx"
    out.unlink(missing_ok=True)
    script = stitch_script(plan, fragments, out)

    written = asyncio.run(run_node_script(script, timeout=60))
    try:
        assert written.exists(), f"Output file missing: {written}"
        assert written.stat().st_size > 1000, "Generated pptx is suspiciously small"
    finally:
        written.unlink(missing_ok=True)


# ── Sanitizer / validator ─────────────────────────────────────────────────────

def test_sanitize_strips_markdown_fences():
    raw = "```javascript\nconst slide = pres.addSlide();\n```"
    assert _sanitize_body(raw).strip() == "const slide = pres.addSlide();"


def test_sanitize_unwraps_function_definition():
    raw = "async function addSlide_2(pres) {\n  const slide = pres.addSlide();\n}"
    out = _sanitize_body(raw)
    assert "function" not in out
    assert "pres.addSlide()" in out


def test_validate_rejects_require_and_import():
    with pytest.raises(ValueError):
        _validate_body('const x = require("foo");\nconst slide = pres.addSlide();')
    with pytest.raises(ValueError):
        _validate_body('import foo from "foo";\nconst slide = pres.addSlide();')


def test_validate_requires_addslide_call():
    with pytest.raises(ValueError):
        _validate_body('const slide = "no addSlide here";')


def test_validate_rejects_empty():
    with pytest.raises(ValueError):
        _validate_body("   \n  ")


# ── Placeholder fallback ──────────────────────────────────────────────────────

def test_placeholder_body_is_valid():
    plan = _fake_plan(1)
    spec = plan.slides[0]
    body = _placeholder_body(spec, plan)
    _validate_body(body)  # must satisfy the validator
    assert "failed to render" in body


# ── Forced-failure: retry + placeholder fallback ──────────────────────────────

class _AlwaysFailStructured:
    """Stub for `llm.with_structured_output(...)` that always raises."""

    def __init__(self, raise_msg: str = "stubbed failure"):
        self.calls = 0
        self.raise_msg = raise_msg

    async def ainvoke(self, _messages):
        self.calls += 1
        raise RuntimeError(self.raise_msg)


class _StubLLM:
    def __init__(self, structured):
        self._structured = structured

    def with_structured_output(self, _schema):
        return self._structured


def test_retry_then_placeholder_on_worker_failure(monkeypatch):
    plan = _fake_plan(1)
    spec = plan.slides[0]

    stub_struct = _AlwaysFailStructured()
    monkeypatch.setattr(
        pipeline, "ChatAnthropic", lambda **kwargs: _StubLLM(stub_struct)
    )

    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    async def go():
        sem = asyncio.Semaphore(1)
        return await _render_one_slide(plan, spec, sem, emit, timeout_s=5)

    body = asyncio.run(go())

    assert stub_struct.calls == 2, "Worker should retry exactly once before falling back"
    assert "failed to render" in body, "Placeholder body should be returned after retries"
    assert any(e.get("type") == "slide_failed" for e in events), "slide_failed event missing"
    assert any(e.get("type") == "slide_started" for e in events), "slide_started event missing"


def test_failed_slide_still_produces_runnable_script(monkeypatch):
    """A failed slide should fall back to a placeholder that still renders."""
    plan = _fake_plan(2)
    stub_struct = _AlwaysFailStructured()
    monkeypatch.setattr(
        pipeline, "ChatAnthropic", lambda **kwargs: _StubLLM(stub_struct)
    )

    async def emit(_event: dict) -> None:
        return None

    async def go():
        return await render_slides_parallel(plan, emit, concurrency=2)

    bodies = asyncio.run(go())
    assert len(bodies) == 2
    for body in bodies:
        _validate_body(body)

    out = REPO_ROOT / "_test_fallback_output.pptx"
    out.unlink(missing_ok=True)
    script = stitch_script(plan, bodies, out)

    if (REPO_ROOT / "node_modules" / "pptxgenjs").exists():
        written = asyncio.run(run_node_script(script, timeout=60))
        try:
            assert written.exists()
            assert written.stat().st_size > 1000
        finally:
            written.unlink(missing_ok=True)


# ── Optional smoke tests (LLM-backed) ─────────────────────────────────────────

@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY") or os.getenv("RUN_SMOKE") != "1",
    reason="Smoke test requires ANTHROPIC_API_KEY and RUN_SMOKE=1",
)
def test_smoke_small_deck():
    from pptx_pipeline import generate_deck

    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    async def go():
        return await generate_deck(
            "Create a short 5-slide deck about the benefits of daily walking.",
            emit,
            concurrency=3,
        )

    path = asyncio.run(go())
    try:
        assert path.exists()
        assert path.stat().st_size > 1000

        plan_events = [e for e in events if e.get("type") == "plan"]
        assert plan_events, "No plan event emitted"
        assert plan_events[0]["slide_count"] >= 3

        terminal = {
            (e["index"]): e["type"]
            for e in events
            if e.get("type") in ("slide_done", "slide_failed")
        }
        assert len(terminal) == plan_events[0]["slide_count"]
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY") or os.getenv("RUN_SMOKE_LARGE") != "1",
    reason="Large smoke test requires ANTHROPIC_API_KEY and RUN_SMOKE_LARGE=1",
)
def test_smoke_large_deck_30_slides():
    """End-to-end 30-slide deck. Slow and costly; opt-in only."""
    from pptx_pipeline import generate_deck

    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    async def go():
        return await generate_deck(
            "Create a comprehensive 30-slide presentation about the history of "
            "computing, from the abacus to modern AI. Each slide must cover a "
            "distinct era or breakthrough.",
            emit,
            concurrency=5,
        )

    path = asyncio.run(go())
    try:
        assert path.exists()
        assert path.stat().st_size > 5000

        plan_events = [e for e in events if e.get("type") == "plan"]
        assert plan_events
        slide_count = plan_events[0]["slide_count"]
        assert slide_count >= 20, f"Planner produced only {slide_count} slides for a 30-slide request"

        terminal = {
            e["index"]: e["type"]
            for e in events
            if e.get("type") in ("slide_done", "slide_failed")
        }
        assert len(terminal) == slide_count

        failed = sum(1 for v in terminal.values() if v == "slide_failed")
        assert failed <= max(1, slide_count // 10), f"Too many failed slides: {failed}/{slide_count}"
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
