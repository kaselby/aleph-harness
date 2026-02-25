#!/usr/bin/env python3
"""Create the ~/.aleph/ directory structure and assemble the system prompt."""

import shutil
from pathlib import Path

ALEPH_HOME = Path.home() / ".aleph"
REPO_ROOT = Path(__file__).resolve().parent.parent

DIRS = [
    "tools",
    "skills",
    "docs",
    "inbox",
    "scratch",
    "harness",
]


def assemble_system_prompt() -> str:
    """Build the final system prompt by inserting tool descriptions into the template."""
    template = (REPO_ROOT / "defaults" / "ALEPH.md").read_text()

    # Gather tool descriptions from defaults/tools/ in sorted order
    tools_dir = REPO_ROOT / "defaults" / "tools"
    tool_sections = []
    if tools_dir.exists():
        for tool_file in sorted(tools_dir.glob("*.md")):
            tool_sections.append(tool_file.read_text().rstrip())

    tool_text = "\n\n".join(tool_sections) if tool_sections else "(No tool descriptions found.)"
    return template.replace("{{TOOL_DESCRIPTIONS}}", tool_text)


def scaffold():
    """Create the ~/.aleph/ directory structure."""
    print(f"Creating Aleph home at {ALEPH_HOME}")

    # Create directories
    for d in DIRS:
        path = ALEPH_HOME / d
        path.mkdir(parents=True, exist_ok=True)
        print(f"  Created {path}")

    # Assemble and write system prompt
    dst_prompt = ALEPH_HOME / "ALEPH.md"
    if dst_prompt.exists():
        print(f"  Skipped {dst_prompt} (already exists)")
    else:
        prompt = assemble_system_prompt()
        dst_prompt.write_text(prompt)
        print(f"  Assembled {dst_prompt}")

    # Create empty memory.md if it doesn't exist
    memory = ALEPH_HOME / "memory.md"
    if memory.exists():
        print(f"  Skipped {memory} (already exists)")
    else:
        memory.write_text("")
        print(f"  Created {memory}")

    # Create empty tools/REGISTRY.md if it doesn't exist
    registry = ALEPH_HOME / "tools" / "REGISTRY.md"
    if registry.exists():
        print(f"  Skipped {registry} (already exists)")
    else:
        registry.write_text("")
        print(f"  Created {registry}")

    # Copy harness source code
    src_dir = REPO_ROOT / "src" / "aleph"
    dst_dir = ALEPH_HOME / "harness"
    if src_dir.exists():
        for py_file in src_dir.glob("*.py"):
            shutil.copy2(py_file, dst_dir / py_file.name)
        print(f"  Copied harness source to {dst_dir}")

    # Copy default skills
    skills_src = REPO_ROOT / "defaults" / "skills"
    skills_dst = ALEPH_HOME / "skills"
    if skills_src.exists():
        for skill_dir in skills_src.iterdir():
            if skill_dir.is_dir():
                dst = skills_dst / skill_dir.name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(skill_dir, dst)
                print(f"  Copied skill: {skill_dir.name}")

    print("\nDone. Run `aleph` to start a session.")


if __name__ == "__main__":
    scaffold()
