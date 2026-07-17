# Codex-first README Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `bright-coder/scroll-world` installable and verifiable by a new Codex user directly from the repository README.

**Architecture:** Keep installation guidance in the root `README.md`, with Codex as the primary path and development/other-agent paths secondary. Extend the existing documentation contract test so stale upstream URLs, missing setup steps, or unsafe live-generation verification fail offline.

**Tech Stack:** Markdown, Vercel Skills CLI, Python `unittest`, existing `kie_client.py`, FFmpeg/FFprobe.

## Global Constraints

- The primary install command is `npx skills add bright-coder/scroll-world --skill scroll-world -g -a codex -y`.
- Commands reference `bright-coder/scroll-world`, not `oso95/scroll-world`.
- `.env.local` belongs in the user's generated-web project, not the global skill directory.
- Secret examples contain placeholders only and never print the API key.
- Installation verification is offline and must not submit a Kie.ai task.
- Node.js 18+, Python 3, FFmpeg/FFprobe, Codex built-in `image_gen`, and a funded Kie.ai key are stated prerequisites.

---

### Task 1: Codex-first installation guide

**Files:**
- Modify: `skills/scroll-world/tests/test_kie_client.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the existing `DocumentationTests` class and root README.
- Produces: a tested README contract for installation, configuration, first use, development, maintenance, other agents, and troubleshooting.

- [ ] **Step 1: Extend the documentation contract test**

Add assertions to `DocumentationTests.test_default_provider_and_commands_are_documented` or a focused sibling test that reads `README.md` and requires these exact fragments:

```python
required_readme_fragments = (
    "npx skills add bright-coder/scroll-world --skill scroll-world -g -a codex -y",
    "cp .env.example .env.local",
    "KIE_API_KEY=replace-with-your-kie-api-key",
    "npx skills list -g -a codex",
    "python3 skills/scroll-world/scripts/kie_client.py --help",
    "python3 -m unittest discover -s skills/scroll-world/tests -v",
    "$scroll-world",
)
for fragment in required_readme_fragments:
    self.assertIn(fragment, readme)
self.assertNotIn("npx skills add oso95/scroll-world", readme)
self.assertNotIn("git clone https://github.com/oso95/scroll-world", readme)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest skills.scroll-world.tests.test_kie_client.DocumentationTests -v
```

If Python module naming does not accept the hyphenated directory, run:

```bash
python3 -m unittest discover -s skills/scroll-world/tests -p 'test_kie_client.py' -v
```

Expected: FAIL because the current README still references `oso95/scroll-world` and lacks the complete Codex setup contract.

- [ ] **Step 3: Rewrite the README installation section**

Replace the current install/requirements area with this ordered onboarding flow:

1. `## Quick start — Codex`
2. `### 1. Install prerequisites`
3. `### 2. Install the skill globally`
4. `### 3. Verify the installation`
5. `### 4. Configure a project`
6. `### 5. Start in Codex`
7. `## Install for development`
8. `## Update or uninstall`
9. `## Other agents`
10. `## Troubleshooting`

Use these exact primary commands:

```bash
npx skills add bright-coder/scroll-world --skill scroll-world -g -a codex -y
npx skills list -g -a codex
```

For project-local configuration, show:

```bash
cp .env.example .env.local
```

```dotenv
KIE_API_KEY=replace-with-your-kie-api-key
KIE_MODEL=bytedance/seedance-2-fast
```

State that `.env.local` must stay uncommitted and belongs in the project where Codex is building the scroll site. Verify local tools without a paid request:

```bash
python3 --version
ffmpeg -version
ffprobe -version
python3 skills/scroll-world/scripts/kie_client.py --help
```

Give a first-use example that invokes `$scroll-world` and explicitly says Codex should ask for budget approval before Kie video generation.

For contributors, use:

```bash
git clone https://github.com/bright-coder/scroll-world.git
cd scroll-world
npx skills add ./skills/scroll-world --skill scroll-world -g -a codex -y
python3 -m unittest discover -s skills/scroll-world/tests -v
```

For maintenance, document the official CLI commands:

```bash
npx skills update scroll-world -g -y
npx skills remove scroll-world -g -a codex -y
```

Retain Claude Code/manual instructions as secondary paths and replace every upstream install/clone URL with `bright-coder/scroll-world`.

Troubleshooting must cover:

- `ffmpeg` or `ffprobe` not found: install FFmpeg and restart the terminal/Codex.
- Python `CERTIFICATE_VERIFY_FAILED`: use a Python installation with a current CA bundle; do not disable TLS verification.
- Kie upload HTTP 403: verify the same key with the Kie dashboard/API account and contact Kie support; do not repeatedly submit paid tasks.
- Skill not visible: run `npx skills list -g -a codex`, reinstall, then restart Codex.

- [ ] **Step 4: Run the focused documentation test and verify GREEN**

Run:

```bash
python3 -m unittest discover -s skills/scroll-world/tests -p 'test_kie_client.py' -v
```

Expected: all tests pass, including the new README assertions.

- [ ] **Step 5: Verify help, links, secrets, and repository hygiene**

Run:

```bash
python3 skills/scroll-world/scripts/kie_client.py --help
git grep -n "oso95/scroll-world" -- README.md
git grep -n "KIE_API_KEY=" -- README.md .env.example
git diff --check
git status --short
```

Expected:

- CLI help exits 0 and lists `generate-video` and `wait`.
- The upstream repository grep returns no matches.
- API key matches are placeholders or empty example assignments only.
- Diff check is clean.
- Only the planned README/test/plan changes are present before commit.

- [ ] **Step 6: Commit and push**

```bash
git add README.md skills/scroll-world/tests/test_kie_client.py docs/superpowers/plans/2026-07-17-readme-codex-install.md
git commit -m "docs: add Codex-first installation guide"
git push origin main
```

Expected: `main` is clean and matches `origin/main` after the push.
