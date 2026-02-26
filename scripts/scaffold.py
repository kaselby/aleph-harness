#!/usr/bin/env python3
"""Create the ~/.aleph/ directory structure and assemble the system prompt."""

import os
import shutil
import venv
from pathlib import Path

ALEPH_HOME = Path.home() / ".aleph"
REPO_ROOT = Path(__file__).resolve().parent.parent

DIRS = [
    "tools",
    "skills",
    "docs",
    "inbox",
    "scratch",
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


def _create_bin_script(path, comment, module):
    """Create a tool bin/ wrapper script that invokes a lib/ module."""
    if path.exists():
        print(f"  Skipped {path.name} (already exists)")
        return
    # All bin scripts route through the venv python and the lib/ module
    path.write_text(
        f"#!/usr/bin/env bash\n"
        f"{comment}\n"
        f"exec ~/.aleph/venv/bin/python3 ~/.aleph/tools/lib/{module}.py \"$@\"\n"
    )
    path.chmod(0o755)
    print(f"  Created tools/bin/{path.name}")


def scaffold():
    """Create the ~/.aleph/ directory structure."""
    print(f"Creating Aleph home at {ALEPH_HOME}")
    ALEPH_HOME.mkdir(parents=True, exist_ok=True)

    # Create directories
    for d in DIRS:
        path = ALEPH_HOME / d
        path.mkdir(parents=True, exist_ok=True)
        print(f"  Created {path}")

    # Symlink harness/ to the repo so edits are live and git works
    harness_link = ALEPH_HOME / "harness"
    if harness_link.is_symlink():
        current_target = harness_link.resolve()
        if current_target == REPO_ROOT.resolve():
            print(f"  Symlink {harness_link} -> {REPO_ROOT} (already correct)")
        else:
            harness_link.unlink()
            os.symlink(REPO_ROOT, harness_link)
            print(f"  Symlink {harness_link} -> {REPO_ROOT} (updated from {current_target})")
    elif harness_link.exists():
        # Old-style copy — remove and replace with symlink
        shutil.rmtree(harness_link)
        os.symlink(REPO_ROOT, harness_link)
        print(f"  Symlink {harness_link} -> {REPO_ROOT} (replaced copy)")
    else:
        os.symlink(REPO_ROOT, harness_link)
        print(f"  Symlink {harness_link} -> {REPO_ROOT}")

    # Create venv if it doesn't exist
    venv_path = ALEPH_HOME / "venv"
    if venv_path.exists():
        print(f"  Skipped {venv_path} (already exists)")
    else:
        print(f"  Creating venv at {venv_path}...")
        venv.create(venv_path, with_pip=True)
        print(f"  Created {venv_path}")

    # Assemble and write system prompt (always overwrite)
    dst_prompt = ALEPH_HOME / "ALEPH.md"
    prompt = assemble_system_prompt()
    dst_prompt.write_text(prompt)
    print(f"  Assembled {dst_prompt}")

    # Create memory directory structure
    memory_dir = ALEPH_HOME / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "sessions").mkdir(exist_ok=True)
    for name in ["context.md", "preferences.md", "patterns.md"]:
        f = memory_dir / name
        if not f.exists():
            f.write_text("")
            print(f"  Created {f}")
        else:
            print(f"  Skipped {f} (already exists)")

    # Create empty tools/REGISTRY.md if it doesn't exist
    registry = ALEPH_HOME / "tools" / "REGISTRY.md"
    if registry.exists():
        print(f"  Skipped {registry} (already exists)")
    else:
        registry.write_text("")
        print(f"  Created {registry}")

    # Deploy tool framework via symlinks.
    # lib/ is symlinked as a whole directory — it's entirely harness infrastructure.
    # bin/ and definitions/ are agent-owned; scaffold just ensures they exist.
    tools_src = REPO_ROOT / "defaults" / "tools"
    tools_dst = ALEPH_HOME / "tools"

    # Symlink lib/ directory
    lib_src = tools_src / "lib"
    lib_dst = tools_dst / "lib"
    if lib_src.exists():
        if lib_dst.is_symlink():
            if lib_dst.resolve() == lib_src.resolve():
                print(f"  Symlink tools/lib/ (already correct)")
            else:
                lib_dst.unlink()
                os.symlink(lib_src, lib_dst)
                print(f"  Symlink tools/lib/ (updated)")
        else:
            # Replace copied dir with symlink
            if lib_dst.exists():
                shutil.rmtree(lib_dst)
            os.symlink(lib_src, lib_dst)
            print(f"  Symlink tools/lib/ -> {lib_src}")

    # Ensure bin/ and definitions/ directories exist (agent-owned)
    for subdir in ["bin", "definitions"]:
        d = tools_dst / subdir
        d.mkdir(parents=True, exist_ok=True)

    # Ensure definitions/__init__.py exists (plain file, not symlink)
    init_file = tools_dst / "definitions" / "__init__.py"
    if not init_file.exists() or init_file.is_symlink():
        if init_file.is_symlink():
            init_file.unlink()
        init_file.write_text("")

    # Create infra bin scripts if they don't exist
    _create_bin_script(
        tools_dst / "bin" / "tool-budget",
        "# View and manage the paid tool budget.",
        "budget_cli",
    )
    _create_bin_script(
        tools_dst / "bin" / "tool-run",
        "# Run any registered tool by name.",
        "runner",
    )

    # Create usage directory for budget tracking
    usage_dir = ALEPH_HOME / "usage"
    usage_dir.mkdir(parents=True, exist_ok=True)

    # Create credentials directory for API keys
    creds_dir = ALEPH_HOME / "credentials"
    creds_dir.mkdir(parents=True, exist_ok=True)

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
