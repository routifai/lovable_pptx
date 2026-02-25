"""
mcp_server.py — FastMCP server exposing Agent Skills as MCP tools.

Works natively with:
  • agent.py (via MultiServerMCPClient)
  • Cursor       → add to .cursor/mcp.json
  • Claude Desktop → add to claude_desktop_config.json
  • Any other MCP-compatible client

Install:
  pip install "mcp[cli]" pyyaml
  npm install -g pptxgenjs          # needed by the PPTX skill
  git clone https://github.com/anthropics/skills ./skills

Run:
  python mcp_server.py
  # → listening on http://127.0.0.1:8001/mcp
"""

import re
import os
import sys
import subprocess
import tempfile
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

# ── Config ────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent.resolve()
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", str(_HERE / "skills" / "skills")))
HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8001"))

# ── Skill Registry ────────────────────────────────────────────────────────────

def parse_skill_md(skill_dir: Path) -> dict | None:
    """Parse a SKILL.md file — metadata only (progressive disclosure step 1)."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None

    text = skill_md.read_text()
    fm = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if fm:
        meta = yaml.safe_load(fm.group(1))
        body = fm.group(2).strip()
    else:
        meta = {"name": skill_dir.name, "description": ""}
        body = text

    refs = {
        f.name: f.read_text()
        for f in skill_dir.glob("*.md")
        if f.name != "SKILL.md"
    }

    return {
        "name":         meta.get("name", skill_dir.name),
        "description":  meta.get("description", ""),
        "instructions": body,
        "references":   refs,
        "scripts_dir":  str(skill_dir / "scripts"),
        "dir":          str(skill_dir),
    }


REGISTRY: dict[str, dict] = {}
if SKILLS_DIR.exists():
    for folder in SKILLS_DIR.iterdir():
        skill = parse_skill_md(folder)
        if skill:
            REGISTRY[skill["name"]] = skill
            print(f"  [skill] registered: {skill['name']}")
else:
    print(f"  [warn] skills dir not found: {SKILLS_DIR}")

# ── FastMCP Server ────────────────────────────────────────────────────────────

mcp = FastMCP(
    "Document Skills Server",
    instructions=(
        "Creates and edits PowerPoint, Word, Excel, and PDF files "
        "using Anthropic's open-source Agent Skills."
    ),
    host=HOST,
    port=PORT,
)

# ── Tool 1: list_skills ───────────────────────────────────────────────────────

@mcp.tool()
def list_skills() -> str:
    """
    List all available skills with their names and descriptions.
    Call this first to discover what document types can be created.
    """
    if not REGISTRY:
        return "No skills found. Clone https://github.com/anthropics/skills to ./skills"

    lines = ["Available skills:\n"]
    for name, s in REGISTRY.items():
        desc = s["description"][:80] + "..." if len(s["description"]) > 80 else s["description"]
        lines.append(f"  • {name}: {desc}")
    return "\n".join(lines)


# ── Tool 2: load_skill ────────────────────────────────────────────────────────

@mcp.tool()
def load_skill(skill_name: str) -> str:
    """
    Load the full instructions for a skill (progressive disclosure step 2).
    Call this when you're about to use a skill to complete a task.

    Args:
        skill_name: One of: pptx, docx, xlsx, pdf
    """
    skill = REGISTRY.get(skill_name)
    if not skill:
        available = list(REGISTRY.keys())
        return f"Skill '{skill_name}' not found. Available: {available}"

    refs = "\n\n".join(
        f"=== {name} ===\n{content}"
        for name, content in skill["references"].items()
    )

    return f"""# Skill: {skill['name']}

{skill['instructions']}

## Reference Files
{refs}

## Scripts Directory
{skill['scripts_dir']}
"""


# ── Tool 3: create_presentation ──────────────────────────────────────────────

@mcp.tool()
def create_presentation(
    topic: str,
    slide_count: int = 5,
    output_filename: str = "presentation.pptx",
    output_dir: str = ".",
    extra_instructions: str = "",
) -> str:
    """
    Create a PowerPoint presentation (.pptx) from a topic description.
    Uses the PPTX Agent Skill under the hood (pptxgenjs).

    Args:
        topic:              What the presentation should be about
        slide_count:        Number of slides to generate (default: 5)
        output_filename:    Output file name, must end in .pptx
        output_dir:         Directory to save the file (default: current dir)
        extra_instructions: Any extra requirements (e.g. "formal tone", "include charts")

    Returns:
        Absolute path to the saved .pptx file, or an error message.
    """
    skill = REGISTRY.get("pptx")
    if not skill:
        return "Error: PPTX skill not found. Clone https://github.com/anthropics/skills"

    output_path = Path(output_dir) / output_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        js_code = _generate_pptx_js(topic, slide_count, str(output_path.resolve()), extra_instructions)
        with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
            f.write(js_code)
            tmp_path = f.name

        result = subprocess.run(
            ["node", tmp_path],
            capture_output=True, text=True, timeout=60
        )
        os.unlink(tmp_path)

        if output_path.exists():
            return f"✅ Saved: {output_path.resolve()} ({output_path.stat().st_size // 1024}KB)"

        return f"❌ Node.js ran but file not found.\nstdout: {result.stdout}\nstderr: {result.stderr}"

    except FileNotFoundError:
        return "❌ Node.js not installed. Run: brew install node"
    except subprocess.TimeoutExpired:
        return "❌ Timed out after 60s."
    except Exception as e:
        return f"❌ Error: {e}"


def _generate_pptx_js(topic: str, slide_count: int, output_path: str, extra: str = "") -> str:
    """
    Minimal pptxgenjs template. In production the LLM generates this
    from the full SKILL.md instructions via run_node_script.
    """
    slide_titles = [
        "Introduction",
        "Overview",
        "Key Points",
        "Details",
        "Conclusion",
    ]
    # Pad or trim to requested count
    while len(slide_titles) < slide_count:
        slide_titles.append(f"Slide {len(slide_titles) + 1}")
    slide_titles = slide_titles[:slide_count]

    slides_js = "\n\n".join([
        f"""  let slide{i} = pres.addSlide();
  slide{i}.addText("{title}", {{ x: 0.5, y: 0.3, w: "90%", fontSize: 28, bold: true, color: "363636" }});
  slide{i}.addText("{'Topic: ' + topic if i == 0 else title + ' content goes here.'}", {{
    x: 0.5, y: 1.2, w: "90%", h: 3.5, fontSize: 18, color: "595959", valign: "top"
  }});"""
        for i, title in enumerate(slide_titles)
    ])

    # Fix the f-string interpolation issue for JS template
    slides_js = slides_js.replace("'Topic: ' + topic if i == 0 else title + ' content goes here.'",
                                   f"{topic}" if True else "")
    # Rebuild cleanly
    slides_js_parts = []
    for i, title in enumerate(slide_titles):
        body_text = topic if i == 0 else f"{title} — {extra or 'Content goes here.'}"
        slides_js_parts.append(
            f'  let slide{i} = pres.addSlide();\n'
            f'  slide{i}.addText("{title}", {{ x: 0.5, y: 0.3, w: "90%", fontSize: 28, bold: true, color: "363636" }});\n'
            f'  slide{i}.addText("{body_text}", {{ x: 0.5, y: 1.2, w: "90%", h: 3.5, fontSize: 18, color: "595959", valign: "top" }});'
        )

    return f"""const pptxgen = require("pptxgenjs");
const pres = new pptxgen();

pres.layout = "LAYOUT_WIDE";
pres.title  = "{topic}";

{chr(10).join(slides_js_parts)}

pres.writeFile({{ fileName: "{output_path}" }})
  .then(() => console.log("Saved: {output_path}"))
  .catch(err => {{ console.error("Error:", err); process.exit(1); }});
"""


# ── Tool 4: run_node_script ───────────────────────────────────────────────────

@mcp.tool()
def run_node_script(code: str, output_hint: str = "") -> str:
    """
    Execute a Node.js script directly. Use this when the LLM has generated
    pptxgenjs code via the PPTX skill and wants to run it.

    Args:
        code:        Complete Node.js script to execute
        output_hint: Expected output filename (for confirmation message)
    """
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(code)
        tmp = f.name
    try:
        result = subprocess.run(
            ["node", tmp], capture_output=True, text=True, timeout=60
        )
        output = (result.stdout + result.stderr).strip()
        if output_hint and Path(output_hint).exists():
            size = Path(output_hint).stat().st_size // 1024
            return f"✅ {output_hint} ({size}KB)\n{output}"
        return output or "Script completed (no output)."
    except FileNotFoundError:
        return "❌ node not found. Install Node.js."
    except subprocess.TimeoutExpired:
        return "❌ Timeout after 60s."
    finally:
        os.unlink(tmp)


# ── Tool 5: run_python_script ─────────────────────────────────────────────────

@mcp.tool()
def run_python_script(code: str) -> str:
    """
    Execute a Python script for post-processing tasks,
    e.g. editing existing .pptx files via python-pptx.

    Args:
        code: Complete Python script to execute
    """
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        tmp = f.name
    try:
        result = subprocess.run(
            [sys.executable, tmp], capture_output=True, text=True, timeout=60
        )
        return (result.stdout + result.stderr).strip() or "Script completed (no output)."
    except subprocess.TimeoutExpired:
        return "❌ Timeout after 60s."
    finally:
        os.unlink(tmp)


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    stdio_mode = "--stdio" in sys.argv

    if stdio_mode:
        # Claude Desktop: silent stdio mode (no prints — they corrupt the protocol)
        mcp.run(transport="stdio")
    else:
        print(f"\n🚀 Document Skills MCP Server")
        print(f"   Transport : streamable-http")
        print(f"   Endpoint  : http://{HOST}:{PORT}/mcp")
        print(f"   Skills    : {list(REGISTRY.keys()) or 'none — clone anthropics/skills'}")
        print(f"\n   Add to Cursor (.cursor/mcp.json):")
        print(f'   {{"doc-skills": {{"url": "http://{HOST}:{PORT}/mcp", "transport": "http"}}}}')
        print(f"\n   Add to Claude Desktop (claude_desktop_config.json):")
        print(f'   {{"doc-skills": {{"command": "{sys.executable}", "args": ["{Path("mcp_server.py").resolve()}", "--stdio"]}}}}')
        print()
        mcp.run(transport="streamable-http")
