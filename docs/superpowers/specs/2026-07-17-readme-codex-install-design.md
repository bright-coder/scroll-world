# Codex-first README installation design

Date: 2026-07-17
Status: approved for planning

## Goal

Make the repository installable by a new Codex user without reading the pipeline internals. The README must lead with a verified global Skills CLI installation, explain the local project configuration needed by Kie.ai, and retain concise paths for contributors and non-Codex agents.

## Audience and default path

The primary audience is a Codex user who wants to invoke `$scroll-world` from any project. The default path is a global install from the repository's `main` branch:

```bash
npx skills add bright-coder/scroll-world --skill scroll-world -g -a codex -y
```

This follows the official Skills CLI owner/repository source format and targets Codex explicitly. The Kie implementation is now merged into `main`, so no branch pin is needed.

## README structure

1. **Codex quick start** — prerequisites, one-command global installation, verification, per-project `.env.local`, and a first `$scroll-world` prompt.
2. **Requirements** — Node.js 18+, Python 3, FFmpeg/FFprobe, Codex built-in `image_gen`, and a funded Kie.ai API key.
3. **Project configuration** — copy `.env.example` to the project-local `.env.local`, set `KIE_API_KEY`, optionally override `KIE_MODEL`, and never commit the secret.
4. **Install for development** — clone `bright-coder/scroll-world`, install the skill from the local path, and run the offline Python test suite.
5. **Update and uninstall** — use the Skills CLI's supported update, list, and remove commands.
6. **Other agents** — keep Claude Code and manual copy instructions as secondary options, using the fork URL rather than the upstream repository.
7. **Troubleshooting** — focused checks for missing FFmpeg/FFprobe, Python CA certificate failures, Kie upload HTTP 403, and locating the installed Codex skill.

## Command and safety rules

- Commands must reference `bright-coder/scroll-world`, not `oso95/scroll-world`.
- Installation examples must distinguish global skill installation from cloning the repository for development.
- `.env.local` is created inside the user's generated-web project, not inside the global skill directory.
- Secret examples use placeholders only. The README must never encourage printing the API key.
- Live Kie generation is not part of installation verification because it can consume credits. Verification is limited to local commands and CLI help/tests.
- The README must state that Kie uploads can be temporary and that completed media should be downloaded promptly, without inventing retention periods that conflict with provider documentation.

## Verification

The documentation change is complete when:

- every repository URL points to the fork where appropriate;
- the Codex install command matches the official Skills CLI syntax;
- all referenced local files and commands exist;
- the Kie client help command succeeds;
- the complete offline suite passes;
- Markdown code fences and links are structurally valid;
- `git diff --check` passes and no secret or local environment file is tracked.

## Out of scope

- Publishing the skill to a separate package registry.
- Automating FFmpeg or Python installation across operating systems.
- Running another paid Kie.ai smoke test.
- Redesigning the skill workflow or provider client.
