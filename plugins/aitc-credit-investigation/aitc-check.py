#!/usr/bin/env python3
"""
AITC Plugin Validator
Checks all plugin files for structural integrity.
Run before committing: python3 aitc-check.py
"""
import os, sys, json
from pathlib import Path

base = Path(__file__).parent
errors = []
checked = 0

def check(condition, message):
    global checked
    checked += 1
    if not condition:
        errors.append(message)
        print(f"  ✗ {message}")
    else:
        print(f"  ✓ {message}")

print(f"[aitc-check] validating plugin at {base}\n")

# plugin.json
pj = base / ".claude-plugin" / "plugin.json"
try:
    data = json.loads(pj.read_text())
    check(True, "plugin.json — valid JSON")
    check("name" in data, "plugin.json — has 'name' field")
    check("version" in data, "plugin.json — has 'version' field")
except Exception as e:
    check(False, f"plugin.json — {e}")

# .mcp.json
mcp = base / ".mcp.json"
try:
    data = json.loads(mcp.read_text())
    check(True, ".mcp.json — valid JSON")
    check("mcpServers" in data, ".mcp.json — has 'mcpServers' field")
    servers = data.get("mcpServers", {})
    check("xbrl-taiwan" in servers, ".mcp.json — has 'xbrl-taiwan' server")
    if "xbrl-taiwan" in servers:
        url = servers["xbrl-taiwan"].get("url", "")
        check("8091" in url, f".mcp.json — URL points to port 8091 ({url})")
except Exception as e:
    check(False, f".mcp.json — {e}")

# Skills
skills_dir = base / "skills"
if skills_dir.exists():
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        check(skill_md.exists(), f"skills/{skill_dir.name}/SKILL.md — exists")
        if skill_md.exists():
            content = skill_md.read_text()
            check(content.startswith("---"), f"skills/{skill_dir.name}/SKILL.md — has YAML frontmatter")
            check("name:" in content, f"skills/{skill_dir.name}/SKILL.md — has 'name' in frontmatter")
            check("description:" in content, f"skills/{skill_dir.name}/SKILL.md — has 'description' in frontmatter")
            check("FIRST:" in content or "FIRST" in content, f"skills/{skill_dir.name}/SKILL.md — has data source priority (FIRST)")
            check("⚠️" in content or "disclaimer" in content.lower() or "免責" in content,
                  f"skills/{skill_dir.name}/SKILL.md — has disclaimer reference")

# Commands
commands_dir = base / "commands"
if commands_dir.exists():
    skill_names = [d.name for d in skills_dir.iterdir() if d.is_dir()] if skills_dir.exists() else []
    for cmd_md in sorted(commands_dir.glob("*.md")):
        content = cmd_md.read_text()
        check(content.startswith("---"), f"commands/{cmd_md.name} — has YAML frontmatter")
        check("description:" in content, f"commands/{cmd_md.name} — has 'description'")

# README
readme = base / "README.md"
check(readme.exists(), "README.md — exists")

# Summary
print(f"\nChecked {checked} item(s).")
if errors:
    print(f"FAILED — {len(errors)} issue(s) found:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
else:
    print("OK — all checks passed, 0 issues.")
