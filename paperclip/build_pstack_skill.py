"""Package an installed pstack plugin as one Paperclip skill.

Paperclip imports every SKILL.md in a directory as its own skill. Importing the
root SKILL.md file instead keeps the bundled skills under references/ as one
package, and their relative links still resolve.
"""
import json
import pathlib
import shutil
import sys

ENTRY = """---
name: pstack
description: Rigorous engineering workflow. Use for non-trivial code, debugging, review, design, or verification work.
---

# pstack {version}

Read `references/skills/poteto-mode/SKILL.md` and follow it. Bundled skills live at `references/skills/<name>/SKILL.md`. Read only the ones the current step needs.

## Inside Paperclip

- Your Paperclip assignment and instructions outrank pstack.
- Do not spawn subagents or multi-model panels unless the task says to. Run each step yourself, because subscription quota is limited.
- Skip steps that need Cursor-only tools, and say so in your issue comment.
- Report on the issue. Open a pull request only when the task asks for one.
"""


def build(plugin: pathlib.Path, out: pathlib.Path) -> int:
    if out.exists():
        shutil.rmtree(out)
    count = 0
    for top in ("skills", "docs"):
        for source in (plugin / top).rglob("*.md"):
            target = out / "references" / source.relative_to(plugin)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            count += 1
    shutil.copyfile(plugin / "LICENSE", out / "references" / "LICENSE.md")
    version = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    (out / "SKILL.md").write_text(ENTRY.format(version=version), encoding="utf-8", newline="\n")
    return count


if __name__ == "__main__":
    print(f"packaged {build(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))} files")
