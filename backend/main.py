import sys
import os
import re
import json
import shutil
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add parent directory to path so we can import agent.py
sys.path.append(str(Path(__file__).parent.parent))

# Import agent_executor directly
try:
    from agent import agent_executor
except Exception as e:
    print(f"Warning: Could not import agent_executor: {e}")
    agent_executor = None

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directory where we copy generated files so they're serveable
GENERATED_DIR = Path(__file__).parent.parent / "generated"
GENERATED_DIR.mkdir(exist_ok=True)


class GenerateRequest(BaseModel):
    prompt: str


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def find_pptx_in_text(text: str) -> Path | None:
    """Scan agent message text for an absolute .pptx path and return it if it exists."""
    matches = re.findall(r"[`'\"]?(/[^\s`'\"]+\.pptx)[`'\"]?", text)
    for match in matches:
        p = Path(match)
        if p.exists():
            return p
    return None


def collect_pptx(agent_messages: list[str]) -> Path | None:
    """
    Try to find the generated .pptx file:
    1. Parse paths mentioned in agent messages
    2. Fall back to most-recently-modified .pptx anywhere the agent might write
    """
    # 1. Parse from agent output text
    for text in agent_messages:
        found = find_pptx_in_text(text)
        if found:
            return found

    # 2. Fallback: most recent .pptx in cwd, /tmp, or home
    search_dirs = [Path("."), Path("/tmp"), Path.home()]
    candidates: list[Path] = []
    for d in search_dirs:
        try:
            candidates.extend(d.glob("*.pptx"))
        except Exception:
            pass
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


async def stream_agent(prompt: str):
    if agent_executor is None:
        yield sse_event({"type": "error", "message": "Agent failed to initialize. Check ANTHROPIC_API_KEY and dependencies."})
        return

    if not os.getenv("ANTHROPIC_API_KEY"):
        yield sse_event({"type": "error", "message": "ANTHROPIC_API_KEY not found in .env file."})
        return

    agent_text_messages: list[str] = []

    try:
        yield sse_event({"type": "status", "message": "Starting agent..."})

        async for chunk in agent_executor.astream(
            {"messages": [{"role": "user", "content": prompt}]}
        ):
            for node_name, node_output in chunk.items():
                messages = node_output.get("messages", []) if isinstance(node_output, dict) else []

                for msg in messages:
                    msg_type = type(msg).__name__

                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        for tc in msg.tool_calls:
                            yield sse_event({
                                "type": "tool_call",
                                "tool": tc.get("name", "unknown"),
                                "message": f"Calling tool: {tc.get('name', 'unknown')}",
                            })

                    elif msg_type == "ToolMessage":
                        content = msg.content if isinstance(msg.content, str) else str(msg.content)
                        preview = content[:200] + ("..." if len(content) > 200 else "")
                        yield sse_event({
                            "type": "tool_result",
                            "tool": getattr(msg, "name", "tool"),
                            "message": f"Tool result: {preview}",
                        })

                    elif msg_type == "AIMessage" and msg.content and not (hasattr(msg, "tool_calls") and msg.tool_calls):
                        content = msg.content if isinstance(msg.content, str) else str(msg.content)
                        agent_text_messages.append(content)
                        yield sse_event({
                            "type": "agent_message",
                            "message": content,
                        })

        # ── Find and copy the generated file ──────────────────────────────────
        source = collect_pptx(agent_text_messages)
        served_name = None

        if source and source.exists():
            dest = GENERATED_DIR / source.name
            shutil.copy2(source, dest)
            served_name = source.name
            yield sse_event({"type": "status", "message": f"File ready: {served_name}"})

        yield sse_event({
            "type": "done",
            "file": served_name,
            "message": "Generation complete!",
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield sse_event({"type": "error", "message": str(e)})


@app.post("/api/generate")
async def generate_presentation(request: GenerateRequest):
    return StreamingResponse(
        stream_agent(request.prompt),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    # Prevent directory traversal
    safe_name = Path(filename).name
    file_path = GENERATED_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File '{safe_name}' not found in generated directory.")
    return FileResponse(
        str(file_path),
        filename=safe_name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
