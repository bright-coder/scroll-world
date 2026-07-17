# Framework output target design

Date: 2026-07-18
Status: approved for planning

## Goal

Make `scroll-world` choose and explain an explicit frontend output target so a user does not unexpectedly receive Plain HTML when they wanted React or Next.js.

## Decision order

The skill resolves the output target in this order:

1. **Honor the user's prompt.** If the user already requested Next.js, React/Vite, Plain HTML, or adaptation into an existing project, do not ask the same question again.
2. **Inspect the current project.** Detect an existing framework from repository files and dependencies.
3. **Confirm a confident detection.** State the detected target in one short sentence and allow correction without presenting an unnecessary menu.
4. **Ask when unresolved.** If the project is empty, ambiguous, or contains multiple plausible frontends, ask the user to choose Existing project, Next.js App Router, React/Vite, or Plain HTML.

The skill must never silently select Plain HTML merely because no framework was named.

## Detection rules

- `next.config.*`, a `next` dependency, or an `app/`/`pages/` Next.js structure indicates Next.js.
- A React dependency plus Vite configuration indicates React/Vite.
- An existing application with another supported frontend should be adapted in place rather than replaced.
- Conflicting or inconclusive signals trigger the output-target question.

The resolved target records:

- framework and routing style;
- TypeScript or JavaScript;
- existing-project adaptation versus new scaffold;
- component/page integration location;
- public media directory.

## Target-specific output

### Next.js App Router

- Produce a Client Component because the scrub engine uses `window`, `document`, video elements, and scroll events.
- Store images, videos, posters, and the portable engine under `public/scroll-world/` unless the project has an established asset convention.
- Integrate the component into the requested `app/**/page.tsx` route.
- Keep server-rendered page metadata and supporting copy outside the client-only boundary where practical.

### React/Vite

- Produce a reusable React component mounted from the existing app entry or requested route.
- Store static media under `public/scroll-world/` unless the project convention differs.
- Initialize the engine client-side after the container exists.

### Plain HTML

- Use `references/index-template.html` plus `references/scrub-engine.js` and a local asset tree.
- Select this only after explicit user choice.

### Existing project

- Follow the project's framework, file organization, styling, and routing conventions.
- Do not scaffold or replace an application when adaptation is sufficient.

## Documentation

`README.md` gains an “Output target” section explaining automatic detection, the confirmation/fallback question, and prompt examples for Next.js App Router and React/Vite. It also describes the expected asset/component layout at a high level.

## Verification

- An offline documentation contract test requires the output-target decision order and Next.js/React prompt examples.
- Existing Kie provider and mobile workflow tests remain green.
- No live image or paid video task is part of verification.
- `git diff --check` and secret checks pass.

## Branch and delivery

Implementation is developed in an isolated worktree, merged into `main`, tested again after merge, and pushed to `origin/main`. This repository has no `master` branch.

## Out of scope

- Rewriting the scrub engine as a React-specific rendering engine.
- Creating a new Next.js or Vite application during this documentation change.
- Running another Kie.ai paid smoke test.
