"""
Parallel slide-generation pipeline.

Two phases:
  1. Planner LLM call -> structured DeckPlan (theme + per-slide specs).
  2. N parallel worker LLM calls (one per slide) -> JS snippets, gathered via
     asyncio.gather with a semaphore. Snippets are stitched into a single
     Node.js script that pptxgenjs runs once to write the .pptx.

Public entry point: `generate_deck(prompt, emit) -> Path`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

from langchain_anthropic import ChatAnthropic

_HERE = Path(__file__).parent.resolve()
SKILL_DIR = _HERE / "skills" / "skills" / "pptx"

# Read skill content once at import time. Workers re-use these.
try:
    _PPTXGENJS_REF = (SKILL_DIR / "pptxgenjs.md").read_text()
except FileNotFoundError:
    _PPTXGENJS_REF = ""

try:
    _SKILL_DESIGN = (SKILL_DIR / "SKILL.md").read_text()
except FileNotFoundError:
    _SKILL_DESIGN = ""


# ── Models ────────────────────────────────────────────────────────────────────

class Theme(BaseModel):
    palette: dict[str, str] = Field(
        description=(
            "Hex colors WITHOUT the '#' prefix. Required keys: "
            "'primary', 'secondary', 'accent', 'background', 'text'."
        )
    )
    fonts: dict[str, str] = Field(
        description="Required keys: 'header', 'body'. e.g. {'header': 'Georgia', 'body': 'Calibri'}"
    )
    layout_size: Literal["LAYOUT_16x9", "LAYOUT_16x10", "LAYOUT_4x3", "LAYOUT_WIDE"] = "LAYOUT_16x9"
    motif: str = Field(
        description=(
            "One distinctive visual element repeated across slides "
            "(e.g. 'icons in colored circles', 'thick left accent bar', 'rounded image frames')."
        )
    )


class SlideSpec(BaseModel):
    index: int = Field(description="1-based slide index.")
    layout: Literal[
        "title",
        "section_header",
        "content",
        "two_column",
        "stat_callout",
        "image_text",
        "comparison",
        "closing",
    ]
    title: str
    bullets: list[str] = []
    stats: list[dict] = Field(
        default_factory=list,
        description="Optional list of {value, label} items for stat callouts.",
    )
    body: str | None = None
    visual_notes: str | None = Field(
        default=None,
        description="Short description of the intended visual element (chart, icon set, image, shape grouping).",
    )


class DeckPlan(BaseModel):
    deck_title: str
    theme: Theme
    slides: list[SlideSpec]


class SlideCode(BaseModel):
    """Output schema for a per-slide worker."""

    js_body: str = Field(
        description=(
            "JavaScript statements that add EXACTLY ONE slide to a variable named `pres`. "
            "Start with `const slide = pres.addSlide();`. "
            "Do NOT define a function, do NOT require/import anything, do NOT use markdown fences."
        )
    )


# ── Emit type ─────────────────────────────────────────────────────────────────

EmitFn = Callable[[dict], Awaitable[None]]


async def _noop_emit(event: dict) -> None:  # pragma: no cover - default
    return None


# ── Planner ───────────────────────────────────────────────────────────────────

PLANNER_SYSTEM = """You are a senior presentation designer.

Given a user request, design a complete slide deck. Your output drives a
parallel rendering pipeline, so every visual decision must be made HERE,
once, so that independently-rendered slides stay visually consistent.

Design rules:
- Pick a bold, content-informed color palette. Don't default to generic blue
  unless the topic genuinely calls for it. The palette should feel designed
  for THIS topic.
- One color dominates (60-70% visual weight), with 1-2 supporting tones and
  one sharp accent.
- Dark/light contrast: dark backgrounds for title + closing, light for
  content (a "sandwich"), OR commit to dark throughout for a premium feel.
- Commit to ONE distinctive visual motif and repeat it across every slide
  (e.g. icons in colored circles, thick left-side accent bar, rounded image
  frames).
- Choose an interesting font pairing. Avoid Arial defaults.
- Slide count: 3-30. Default 8-12 unless the user specifies otherwise.
- Every slide needs a visual element planned (chart, image, icons, shape
  grouping). Text-only slides are forbidden.
- Vary layouts across the deck; don't repeat the same layout 5 times in a row.

Output a DeckPlan with `theme` and a list of `slides`.
Slides are 1-indexed in the `index` field.
"""


async def plan_deck(prompt: str, emit: EmitFn = _noop_emit) -> DeckPlan:
    await emit({"type": "status", "message": "Planning deck..."})
    llm = ChatAnthropic(
        model="claude-sonnet-4-5",
        temperature=0.4,
        max_tokens=8000,
    )
    structured = llm.with_structured_output(DeckPlan)
    plan: DeckPlan = await structured.ainvoke(
        [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": prompt},
        ]
    )
    plan.slides = [
        SlideSpec(**{**s.model_dump(), "index": i})
        for i, s in enumerate(plan.slides, start=1)
    ]
    await emit(
        {
            "type": "plan",
            "deck_title": plan.deck_title,
            "slide_count": len(plan.slides),
            "theme": plan.theme.model_dump(),
            "outline": [
                {"index": s.index, "title": s.title, "layout": s.layout}
                for s in plan.slides
            ],
        }
    )
    return plan


# ── Worker ────────────────────────────────────────────────────────────────────

WORKER_SYSTEM = """You generate ONE slide of a presentation using pptxgenjs.

You will receive:
  - A shared theme (palette, fonts, motif, layout size).
  - This slide's spec (layout, title, content).
  - A pptxgenjs cheat sheet.

Strict output rules:
- Return JavaScript statements that add EXACTLY ONE slide to a variable
  named `pres`.
- Start with: `const slide = pres.addSlide();`
- You MAY use `await` (the wrapper function is async).
- DO NOT wrap your code in a function definition.
- DO NOT include require/import statements.
- DO NOT add other slides.
- DO NOT use markdown code fences.
- USE the theme palette, fonts, and motif for every visual choice. The
  slide must look like it belongs to the same deck as its neighbours.
- Hex colors WITHOUT the '#' prefix (e.g. "1E2761" not "#1E2761").
- For bullets use `{bullet: true, breakLine: true}` arrays — never Unicode dots.
- NEVER put accent lines directly under titles. Use whitespace or a
  background block instead.
- Every slide MUST have a visual element (shape grouping, chart, icons,
  image, stat callout, etc.). Text-only is forbidden.
- Stay within a 10in x 5.625in canvas (LAYOUT_16x9). Keep 0.5in margins.

Return the code via the `SlideCode` schema's `js_body` field.
"""


def _build_worker_user_prompt(
    plan: DeckPlan, spec: SlideSpec, retry_error: str | None = None
) -> str:
    parts = [
        "## Shared theme",
        "```json",
        json.dumps(plan.theme.model_dump(), indent=2),
        "```",
        "",
        f"Deck title: {plan.deck_title}",
        f"Total slides in deck: {len(plan.slides)}",
        "",
        "## This slide's spec",
        "```json",
        json.dumps(spec.model_dump(), indent=2),
        "```",
        "",
        "## pptxgenjs reference",
        _PPTXGENJS_REF,
    ]
    if retry_error:
        parts.extend(
            [
                "",
                "## Previous attempt failed",
                "Your previous attempt errored with:",
                "```",
                retry_error,
                "```",
                "Try again. Follow ALL strict output rules above.",
            ]
        )
    return "\n".join(parts)


def _placeholder_body(spec: SlideSpec, plan: DeckPlan) -> str:
    """Last-resort fallback so the deck still renders if a worker keeps failing."""
    bg = plan.theme.palette.get("background", "FFFFFF")
    text_color = plan.theme.palette.get("text", "222222")
    accent = plan.theme.palette.get("accent", "888888")
    title = json.dumps(spec.title)
    note = json.dumps(f"(slide {spec.index} failed to render)")
    return f"""  const slide = pres.addSlide();
  slide.background = {{ color: {json.dumps(bg)} }};
  slide.addShape(pres.shapes.RECTANGLE, {{
    x: 0.5, y: 0.6, w: 0.12, h: 0.7,
    fill: {{ color: {json.dumps(accent)} }}, line: {{ type: "none" }}
  }});
  slide.addText({title}, {{
    x: 0.8, y: 0.6, w: 8.7, h: 0.8,
    fontSize: 32, bold: true, color: {json.dumps(text_color)}, margin: 0
  }});
  slide.addText({note}, {{
    x: 0.8, y: 2, w: 8.7, h: 0.5,
    fontSize: 14, italic: true, color: {json.dumps(text_color)}
  }});
"""


async def _render_one_slide(
    plan: DeckPlan,
    spec: SlideSpec,
    sem: asyncio.Semaphore,
    emit: EmitFn,
    timeout_s: float = 90.0,
) -> str:
    async with sem:
        total = len(plan.slides)
        await emit(
            {
                "type": "slide_started",
                "index": spec.index,
                "total": total,
                "title": spec.title,
            }
        )
        start = time.time()
        llm = ChatAnthropic(
            model="claude-sonnet-4-5",
            temperature=0.3,
            max_tokens=4000,
        )
        structured = llm.with_structured_output(SlideCode)

        async def _try(error: str | None) -> str:
            user = _build_worker_user_prompt(plan, spec, retry_error=error)
            result: SlideCode = await asyncio.wait_for(
                structured.ainvoke(
                    [
                        {"role": "system", "content": WORKER_SYSTEM},
                        {"role": "user", "content": user},
                    ]
                ),
                timeout=timeout_s,
            )
            body = result.js_body or ""
            body = _sanitize_body(body)
            _validate_body(body)
            return body

        try:
            body = await _try(None)
        except Exception as e1:
            try:
                body = await _try(f"{type(e1).__name__}: {e1}")
            except Exception as e2:
                elapsed = time.time() - start
                await emit(
                    {
                        "type": "slide_failed",
                        "index": spec.index,
                        "total": total,
                        "error": f"{type(e2).__name__}: {e2}",
                        "elapsed_s": round(elapsed, 2),
                    }
                )
                return _placeholder_body(spec, plan)

        elapsed = time.time() - start
        await emit(
            {
                "type": "slide_done",
                "index": spec.index,
                "total": total,
                "elapsed_s": round(elapsed, 2),
            }
        )
        return body


def _sanitize_body(body: str) -> str:
    """Strip markdown fences and stray function wrappers the LLM may sneak in."""
    body = body.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", body)
        body = re.sub(r"\n```\s*$", "", body)
    m = re.match(
        r"^\s*(?:async\s+)?function\s+addSlide_\d+\s*\(\s*pres\s*\)\s*\{(.*)\}\s*$",
        body,
        flags=re.DOTALL,
    )
    if m:
        body = m.group(1).strip("\n")
    return body


def _validate_body(body: str) -> None:
    if not body.strip():
        raise ValueError("Empty slide body.")
    if "pres.addSlide" not in body:
        raise ValueError("Slide body must call `pres.addSlide()`.")
    if re.search(r"\brequire\s*\(", body):
        raise ValueError("Slide body must not contain require() calls.")
    if re.search(r"\bimport\s+", body):
        raise ValueError("Slide body must not contain import statements.")


async def render_slides_parallel(
    plan: DeckPlan,
    emit: EmitFn = _noop_emit,
    concurrency: int | None = None,
) -> list[str]:
    if concurrency is None:
        concurrency = int(os.getenv("PPTX_PARALLELISM", "5"))
    sem = asyncio.Semaphore(concurrency)
    await emit(
        {
            "type": "status",
            "message": f"Rendering {len(plan.slides)} slides in parallel (concurrency={concurrency})...",
        }
    )
    tasks = [
        asyncio.create_task(_render_one_slide(plan, s, sem, emit))
        for s in plan.slides
    ]
    bodies = await asyncio.gather(*tasks)
    return bodies


# ── Stitcher ──────────────────────────────────────────────────────────────────

def _indent(body: str, spaces: int = 2) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in body.splitlines())


def stitch_script(plan: DeckPlan, fragments: list[str], output_path: Path) -> str:
    fns: list[str] = []
    calls: list[str] = []
    for i, body in enumerate(fragments, start=1):
        fns.append(f"async function addSlide_{i}(pres) {{\n{_indent(body, 2)}\n}}")
        calls.append(f"  await addSlide_{i}(pres);")
    return f"""const pptxgen = require("pptxgenjs");

{(chr(10) + chr(10)).join(fns)}

async function main() {{
  const pres = new pptxgen();
  pres.layout = {json.dumps(plan.theme.layout_size)};
  pres.title = {json.dumps(plan.deck_title)};
  pres.author = "LovablePPTX";
{chr(10).join(calls)}
  const out = await pres.writeFile({{ fileName: {json.dumps(str(output_path))} }});
  console.log("WROTE:" + out);
}}

main().catch(e => {{ console.error(e); process.exit(1); }});
"""


# ── Node runner ───────────────────────────────────────────────────────────────

async def run_node_script(script: str, timeout: float = 120.0) -> Path:
    script_path = _HERE / f"_generated_pptx_{uuid.uuid4().hex[:8]}.js"
    script_path.write_text(script)
    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_HERE),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"Node script timed out after {timeout}s")

        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"Node script exited with code {proc.returncode}.\n{output}")

        m = re.search(r"WROTE:(.+)", output)
        if not m:
            raise RuntimeError(f"Node script did not report output path.\n{output}")
        return Path(m.group(1).strip())
    finally:
        script_path.unlink(missing_ok=True)


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def generate_deck(
    prompt: str,
    emit: EmitFn = _noop_emit,
    concurrency: int | None = None,
) -> Path:
    plan = await plan_deck(prompt, emit)
    fragments = await render_slides_parallel(plan, emit, concurrency=concurrency)

    await emit({"type": "status", "message": "Stitching script..."})
    safe_title = re.sub(r"[^A-Za-z0-9_-]+", "_", plan.deck_title)[:48] or "deck"
    output_path = _HERE / f"{safe_title}_{uuid.uuid4().hex[:6]}.pptx"
    script = stitch_script(plan, fragments, output_path)

    await emit({"type": "status", "message": "Rendering pptx..."})
    return await run_node_script(script)
