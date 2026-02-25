"""
LangChain PPTX Agent — interactive CLI chat
Run:  python agent.py
"""

import re
import os
import subprocess
from pathlib import Path
import yaml
from dotenv import load_dotenv

load_dotenv()

from langchain.tools import tool
from langchain.agents import create_agent
# from langchain.agents import create_tool_calling_agent, AgentExecutor # Removed as these are not available/compatible
from langchain_core.prompts import ChatPromptTemplate

# ── 1. LLM ────────────────────────────────────────────────────────────────────

from langchain_anthropic import ChatAnthropic
llm = ChatAnthropic(model="claude-sonnet-4-5", temperature=0)

# Swap freely — everything else stays the same:
# from langchain_openai import ChatOpenAI
# llm = ChatOpenAI(model="gpt-4o", temperature=0)

# from langchain_google_genai import ChatGoogleGenerativeAI
# llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", temperature=0)

# from langchain_ollama import ChatOllama
# llm = ChatOllama(model="llama3.3", temperature=0)


# ── 2. Skill loader ───────────────────────────────────────────────────────────

# Use an absolute path anchored to this file so it works regardless of cwd
_HERE = Path(__file__).parent.resolve()
SKILLS_DIR = _HERE / "skills" / "skills"

def parse_skill_md(skill_dir: Path) -> dict:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {}
    text = skill_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if fm_match:
        meta = yaml.safe_load(fm_match.group(1))
        body = fm_match.group(2).strip()
    else:
        meta = {"name": skill_dir.name, "description": ""}
        body = text
    references = {}
    for ref in skill_dir.glob("*.md"):
        if ref.name != "SKILL.md":
            references[ref.name] = ref.read_text()
    return {
        "name": meta.get("name", skill_dir.name),
        "description": meta.get("description", ""),
        "instructions": body,
        "references": references,
        "scripts_dir": str(skill_dir / "scripts"),
    }

SKILL_REGISTRY: dict[str, dict] = {}
if SKILLS_DIR.exists():
    for folder in SKILLS_DIR.iterdir():
        if (folder / "SKILL.md").exists():
            skill = parse_skill_md(folder)
            SKILL_REGISTRY[skill["name"]] = skill


# ── 3. Tools ──────────────────────────────────────────────────────────────────

@tool
def load_skill(skill_name: str) -> str:
    """
    Load full instructions for a specialized skill.
    Always call this before creating files with a skill.
    Available: pptx, docx, xlsx, pdf (and more).
    """
    skill = SKILL_REGISTRY.get(skill_name)
    if not skill:
        return f"Skill '{skill_name}' not found. Available: {list(SKILL_REGISTRY.keys())}"
    refs_summary = "\n\n".join([
        f"=== Reference: {name} ===\n{content[:2000]}"
        for name, content in skill["references"].items()
    ])
    return f"""
=== SKILL: {skill['name']} ===
{skill['instructions']}

{refs_summary}

Scripts directory: {skill['scripts_dir']}
"""


@tool
def run_node_script(code: str) -> str:
    """
    Execute a Node.js script (used by the PPTX skill via pptxgenjs).
    The script should save the file and print its path.
    """
    script_path = _HERE / "_generated_pptx.js"
    script_path.write_text(code)
    try:
        result = subprocess.run(
            ["node", str(script_path)],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout + result.stderr
        return output if output else "Script ran successfully (no output)."
    except subprocess.TimeoutExpired:
        return "Error: Script timed out after 30 seconds."
    except FileNotFoundError:
        return "Error: Node.js not found. Install with: brew install node"
    finally:
        script_path.unlink(missing_ok=True)


@tool
def run_python_script(code: str) -> str:
    """
    Execute a Python script for post-processing (e.g. python-pptx edits).
    """
    script_path = _HERE / "_generated_script.py"
    script_path.write_text(code)
    try:
        result = subprocess.run(
            ["python", str(script_path)],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout + result.stderr
    finally:
        script_path.unlink(missing_ok=True)


# ── 4. Agent ──────────────────────────────────────────────────────────────────

tools = [load_skill, run_node_script, run_python_script]

SYSTEM_PROMPT = (
    "You are a professional presentation and document creator. "
    "You have access to specialized skills for creating files. "
    "When asked to create a presentation, ALWAYS call load_skill('pptx') first "
    "to get the full instructions before generating any code."
)

agent_executor = create_agent(
    model=llm,
    tools=tools,
    system_prompt=SYSTEM_PROMPT,
)

# prompt_template = ChatPromptTemplate.from_messages(
#     [
#         (
#             "system",
#             SYSTEM_PROMPT,
#         ),
#         ("placeholder", "{chat_history}"),
#         ("human", "{input}"),
#         ("placeholder", "{agent_scratchpad}"),
#     ]
# )
# 
# agent = create_tool_calling_agent(llm, tools, prompt_template)
# agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# Compatibility wrapper for agent.py's main loop if we want streaming
# But since we replaced the main loop logic, this is fine.
# However, if we want to support streaming properly, we need to adapt.
# For now, let's just make sure the object is named agent_executor



# ── 5. Interactive CLI ────────────────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════════════╗
║          PPTX Agent  ·  claude-sonnet-4-5        ║
║  Type your request, or 'exit' / 'quit' to stop   ║
╚══════════════════════════════════════════════════╝
Skills loaded: {skills}
"""

def main():
    skill_names = ", ".join(sorted(SKILL_REGISTRY.keys())) or "none found"
    print(BANNER.format(skills=skill_names))

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q"}:
            print("Bye!")
            break

        print()
        try:
            # Using invoke directly on the compiled graph
            # Assuming state structure { "messages": ... }
            result = agent_executor.invoke({"messages": [{"role": "user", "content": user_input}]})
            
            # Extract final response from last AI message
            messages = result.get("messages", [])
            final = messages[-1].content if messages else "No response generated."
            
            print(f"\n✅ Agent: {final}\n")
        except Exception as e:
            print(f"\n❌ Error: {e}\n")


if __name__ == "__main__":
    main()
