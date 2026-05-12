import sys
import os
import re
import json
import shutil
import asyncio
import traceback
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.append(str(Path(__file__).parent.parent))

try:
    from agent import agent_executor
except Exception as e:
    print(f"Warning: Could not import agent_executor: {e}")
    agent_executor = None

try:
    from pptx_pipeline import generate_deck
except Exception as e:
    print(f"Warning: Could not import pptx_pipeline: {e}")
    generate_deck = None

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GENERATED_DIR = Path(__file__).parent.parent / "generated"
GENERATED_DIR.mkdir(exist_ok=True)


class GenerateRequest(BaseModel):
    prompt: str


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


# ── Intent routing ────────────────────────────────────────────────────────────

_PPTX_KEYWORDS = re.compile(
    r"\b(pptx|powerpoint|power\s*point|slide(?:s)?|deck|presentation|pitch\s*deck|keynote)\b",
    re.IGNORECASE,
)


def is_pptx_prompt(prompt: str) -> bool:
    return bool(_PPTX_KEYWORDS.search(prompt))


# ── PPTX pipeline streaming ───────────────────────────────────────────────────

async def stream_pptx_pipeline(prompt: str):
    if generate_deck is None:
        yield sse_event(
            {
                "type": "error",
                "message": "PPTX pipeline failed to initialize. Check dependencies.",
            }
        )
        return

    if not os.getenv("ANTHROPIC_API_KEY"):
        yield sse_event(
            {"type": "error", "message": "ANTHROPIC_API_KEY not found in .env file."}
        )
        return

    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    async def emit(event: dict) -> None:
        await queue.put(event)

    async def runner():
        try:
            path = await generate_deck(prompt, emit)
            if path and path.exists():
                dest = GENERATED_DIR / path.name
                shutil.copy2(path, dest)
                await queue.put(
                    {
                        "type": "status",
                        "message": f"File ready: {path.name}",
                    }
                )
                await queue.put(
                    {
                        "type": "done",
                        "file": path.name,
                        "message": "Generation complete!",
                    }
                )
            else:
                await queue.put(
                    {
                        "type": "error",
                        "message": "Pipeline finished but no file was produced.",
                    }
                )
        except Exception as e:
            traceback.print_exc()
            await queue.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            await queue.put(SENTINEL)

    task = asyncio.create_task(runner())

    try:
        while True:
            event = await queue.get()
            if event is SENTINEL:
                break
            yield sse_event(event)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# ── Legacy agent streaming (for non-pptx prompts) ─────────────────────────────

def _find_pptx_in_text(text: str) -> Path | None:
    matches = re.findall(r"[`'\"]?(/[^\s`'\"]+\.pptx)[`'\"]?", text)
    for match in matches:
        p = Path(match)
        if p.exists():
            return p
    return None


def _collect_pptx(agent_messages: list[str]) -> Path | None:
    for text in agent_messages:
        found = _find_pptx_in_text(text)
        if found:
            return found
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


async def stream_legacy_agent(prompt: str):
    if agent_executor is None:
        yield sse_event(
            {
                "type": "error",
                "message": "Agent failed to initialize. Check ANTHROPIC_API_KEY and dependencies.",
            }
        )
        return

    if not os.getenv("ANTHROPIC_API_KEY"):
        yield sse_event(
            {"type": "error", "message": "ANTHROPIC_API_KEY not found in .env file."}
        )
        return

    agent_text_messages: list[str] = []

    try:
        yield sse_event({"type": "status", "message": "Starting agent..."})

        async for chunk in agent_executor.astream(
            {"messages": [{"role": "user", "content": prompt}]}
        ):
            for _, node_output in chunk.items():
                messages = node_output.get("messages", []) if isinstance(node_output, dict) else []

                for msg in messages:
                    msg_type = type(msg).__name__

                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        for tc in msg.tool_calls:
                            yield sse_event(
                                {
                                    "type": "tool_call",
                                    "tool": tc.get("name", "unknown"),
                                    "message": f"Calling tool: {tc.get('name', 'unknown')}",
                                }
                            )

                    elif msg_type == "ToolMessage":
                        content = msg.content if isinstance(msg.content, str) else str(msg.content)
                        preview = content[:200] + ("..." if len(content) > 200 else "")
                        yield sse_event(
                            {
                                "type": "tool_result",
                                "tool": getattr(msg, "name", "tool"),
                                "message": f"Tool result: {preview}",
                            }
                        )

                    elif msg_type == "AIMessage" and msg.content and not (
                        hasattr(msg, "tool_calls") and msg.tool_calls
                    ):
                        content = msg.content if isinstance(msg.content, str) else str(msg.content)
                        agent_text_messages.append(content)
                        yield sse_event(
                            {"type": "agent_message", "message": content}
                        )

        source = _collect_pptx(agent_text_messages)
        served_name = None

        if source and source.exists():
            dest = GENERATED_DIR / source.name
            shutil.copy2(source, dest)
            served_name = source.name
            yield sse_event({"type": "status", "message": f"File ready: {served_name}"})

        yield sse_event(
            {
                "type": "done",
                "file": served_name,
                "message": "Generation complete!",
            }
        )

    except Exception as e:
        traceback.print_exc()
        yield sse_event({"type": "error", "message": str(e)})


# ── Router ────────────────────────────────────────────────────────────────────

async def stream_agent(prompt: str):
    if is_pptx_prompt(prompt) and generate_deck is not None:
        async for event in stream_pptx_pipeline(prompt):
            yield event
    else:
        async for event in stream_legacy_agent(prompt):
            yield event


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
