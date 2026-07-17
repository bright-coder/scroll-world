# Framework Output Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Before editing the skill, read and follow superpowers:writing-skills. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scroll-world` detect, confirm, or ask for the frontend output target and document predictable Next.js, React/Vite, existing-project, and Plain HTML results.

**Architecture:** Add a mandatory output-target decision block to the existing interview without renumbering the mobile/budget flow. Extend the assembly step with target-specific deliverables, mirror the user-facing behavior in README, and enforce the contract through the existing offline documentation tests.

**Tech Stack:** Markdown skill instructions, Python `unittest`, Next.js/React conventions, portable Vanilla JavaScript scrub engine.

## Global Constraints

- Resolution order is user prompt → project inspection → concise confirmation → fallback question.
- Never silently select Plain HTML when the target is unresolved.
- Next.js output uses a Client Component and defaults media to `public/scroll-world/`.
- React/Vite output uses a reusable component and defaults media to `public/scroll-world/`.
- Existing applications are adapted in place rather than replaced.
- Verification is offline; do not invoke image generation or Kie.ai.
- Delivery target is `origin/main`; this repository has no `master` branch.

---

### Task 1: Framework-aware output instructions

**Files:**
- Modify: `skills/scroll-world/tests/test_kie_client.py`
- Modify: `skills/scroll-world/SKILL.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the existing Step 1 interview, Step 7 assembly instructions, and `DocumentationTests`.
- Produces: a deterministic output-target decision contract and target-specific assembly guidance.

- [ ] **Step 1: Read the skill-authoring rules**

Read `superpowers:writing-skills` completely before editing `SKILL.md`. Preserve the existing skill frontmatter, provider defaults, mobile question, and budget approval behavior.

- [ ] **Step 2: Add a failing documentation contract test**

Add a focused test to `DocumentationTests` that reads `SKILL.md` and `README.md` and requires these exact fragments:

```python
required_skill_fragments = (
    "Output target — resolve before the visual interview",
    "user prompt → project inspection → concise confirmation → fallback question",
    "Never silently default to Plain HTML",
    "next.config.*",
    "public/scroll-world/",
    "Next.js App Router",
    "React/Vite",
)
for fragment in required_skill_fragments:
    self.assertIn(fragment, skill)

required_readme_fragments = (
    "## Choose the output target",
    "$scroll-world Build this in Next.js App Router with TypeScript",
    "$scroll-world Build this in React + Vite",
    "public/scroll-world/",
)
for fragment in required_readme_fragments:
    self.assertIn(fragment, readme)
```

- [ ] **Step 3: Run the contract and verify RED**

Run:

```bash
python3 -m unittest discover -s skills/scroll-world/tests -p 'test_kie_client.py' -v
```

Expected: one documentation test fails because the output-target instructions do not yet exist.

- [ ] **Step 4: Add the Step 1 output-target decision block**

Insert a mandatory block near the beginning of `## Step 1 — Interview the user`, before the subject/brand/art questions. It must state the exact resolution order:

```text
user prompt → project inspection → concise confirmation → fallback question
```

Define the behavior:

- If the prompt names Next.js, React/Vite, Plain HTML, or an existing app, honor it and do not ask again.
- Otherwise inspect `package.json`, `next.config.*`, Vite config, and `app/`/`pages/` structure.
- Confident Next.js/React detection receives a one-sentence confirmation that the user can correct.
- Empty, ambiguous, or conflicting projects receive one choice: Existing project, Next.js App Router, React/Vite, or Plain HTML.
- Never silently default to Plain HTML.
- Record framework/router, TypeScript or JavaScript, adaptation or scaffold, integration route/component, and media directory.

Do not renumber the existing subject, brand, art, journey, mobile, and budget list; this avoids breaking Step 1.5 cross-references.

- [ ] **Step 5: Add target-specific Step 7 deliverables**

Before the generic config example in Step 7, document:

- Existing project: preserve its router, styling, and file conventions; adapt rather than replace.
- Next.js App Router: Client Component, assets under `public/scroll-world/` by default, requested `app/**/page.tsx` integration, and server metadata outside the client boundary when practical.
- React/Vite: reusable component, client-side mount after the container exists, and assets under `public/scroll-world/` by default.
- Plain HTML: `index-template.html` plus `scrub-engine.js`, only after explicit selection.

Keep the engine framework-agnostic and do not claim it has been rewritten in React.

- [ ] **Step 6: Add the README output-target section**

Add `## Choose the output target` after the Codex first-use instructions. Explain auto-detection and the fallback question, then include these prompt examples verbatim:

```text
$scroll-world Build this in Next.js App Router with TypeScript
and keep generated media under public/scroll-world/.
```

```text
$scroll-world Build this in React + Vite as a reusable ScrollWorld component
and keep generated media under public/scroll-world/.
```

Show the high-level output layout:

```text
public/scroll-world/
├── images/
├── videos/
└── scrub-engine.js
components/
└── ScrollWorld.tsx
```

State that a detected existing project is adapted in place and that unresolved targets are asked rather than defaulting to HTML.

- [ ] **Step 7: Run the full offline suite and verify GREEN**

Run:

```bash
python3 -m unittest discover -s skills/scroll-world/tests -v
```

Expected: 65 tests pass with zero failures.

- [ ] **Step 8: Verify documentation and repository hygiene**

Run:

```bash
python3 skills/scroll-world/scripts/kie_client.py --help
git diff --check
git grep -n "KIE_API_KEY=" -- README.md .env.example
git status --short
```

Expected: CLI help exits 0; diff check is clean; key matches are placeholders only; only the planned files are modified.

- [ ] **Step 9: Commit the implementation**

```bash
git add README.md skills/scroll-world/SKILL.md skills/scroll-world/tests/test_kie_client.py
git commit -m "docs: add framework output targeting"
```

Expected: the implementation branch is clean after commit.

- [ ] **Step 10: Merge, verify, and push**

Merge the isolated implementation branch into `main`, rerun the complete offline suite on the merged result, remove generated `__pycache__`, then:

```bash
git push origin main
```

Expected: local `main` and `origin/main` resolve to the same commit and the temporary implementation worktree is removed.
