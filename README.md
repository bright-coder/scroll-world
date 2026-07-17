# scroll-world


https://github.com/user-attachments/assets/b08e641e-985b-4bd4-83ff-6750272d0c37


An agent skill — for Claude Code, Codex, and any `SKILL.md`-compatible agent — that
builds an immersive, **scroll-scrubbed "fly through the world" landing page** for any industry or brand — the kind where, as you scroll, a camera flies
from *outside* each scene *into* its interior, then flows on to the next scene with **no
cuts**. One continuous connected flight through a little generated world (think the Emons
logistics site, applied to whatever you want).

## Quick start — Codex

Install the skill once, then use `$scroll-world` from any project in Codex. Still images
use Codex's built-in `image_gen`; video generation defaults to Kie.ai
`bytedance/seedance-2-fast`.

### 1. Install prerequisites

You need:

- [Codex](https://openai.com/codex/) with the built-in `image_gen` tool.
- Node.js 18 or newer so `npx` can run the Skills CLI.
- Python 3.
- `ffmpeg` and `ffprobe`.
- A funded [Kie.ai](https://kie.ai) API key for video generation.

Check the local tools:

```bash
node --version
python3 --version
ffmpeg -version
ffprobe -version
```

### 2. Install the skill globally

The [Skills CLI](https://github.com/vercel-labs/skills) installs this repository's
`scroll-world` skill into Codex:

```bash
npx skills add bright-coder/scroll-world --skill scroll-world -g -a codex -y
```

`-g` makes the skill available across projects, `-a codex` targets Codex, and `-y`
accepts the installation prompts.

### 3. Verify the installation

```bash
npx skills list -g -a codex
```

The output should list `scroll-world`. Restart Codex if it was open during installation.
This check is local and does not create a paid Kie task.

### 4. Configure a project

Open the project where Codex will build the scroll site and create an ignored
`.env.local` there. Do not put the key in the globally installed skill directory.

If the project already contains this repository's example file:

```bash
cp .env.example .env.local
```

Otherwise, create `.env.local` with:

```dotenv
KIE_API_KEY=replace-with-your-kie-api-key
KIE_MODEL=bytedance/seedance-2-fast
```

Keep `.env.local` out of Git and never paste or print the real key in logs. `KIE_MODEL`
is optional; omitting it uses `bytedance/seedance-2-fast`.

### 5. Start in Codex

Open Codex in the configured project and ask:

```text
$scroll-world Build a scroll-through landing page for a parcel delivery company.
Use Codex image generation for the scene stills and Kie.ai for the videos.
Show me the estimated video cost and wait for my approval before generating.
```

The skill interviews you about the scenes and visual direction before generating paid
assets. It must obtain budget approval before submitting Kie video tasks.

## Choose the output target

`scroll-world` does not silently assume Plain HTML. It resolves the frontend in this
order: your prompt, the current project's files and dependencies, a short confirmation,
then a choice only when the project is empty or ambiguous.

- Existing apps are adapted in place without replacing their router or styling setup.
- Next.js App Router output uses a Client Component and defaults generated media to
  `public/scroll-world/`.
- React/Vite output uses a reusable component and defaults generated media to
  `public/scroll-world/`.
- Plain HTML uses the included template and is selected only when you ask for it.

You can make the target explicit in the first prompt:

```text
$scroll-world Build this in Next.js App Router with TypeScript
and keep generated media under public/scroll-world/.
```

```text
$scroll-world Build this in React + Vite as a reusable ScrollWorld component
and keep generated media under public/scroll-world/.
```

A typical React or Next.js delivery looks like:

```text
public/scroll-world/
├── images/
├── videos/
└── scrub-engine.js
components/
└── ScrollWorld.tsx
```

If the skill confidently detects Next.js or React, it states the target briefly and lets
you correct it. If detection is inconclusive, it asks you to choose Existing project,
Next.js App Router, React/Vite, or Plain HTML before assembling the page.

## Install for development

Clone the repository when you want to edit the skill, run its tests, or inspect the Kie
client:

```bash
git clone https://github.com/bright-coder/scroll-world.git
cd scroll-world
npx skills add ./skills/scroll-world --skill scroll-world -g -a codex -y
python3 -m unittest discover -s skills/scroll-world/tests -v
```

Inspect the client commands without making a network request or spending credits:

```bash
python3 skills/scroll-world/scripts/kie_client.py --help
```

## Update or uninstall

Update the installed skill from its recorded source:

```bash
npx skills update scroll-world -g -y
```

Remove it from Codex:

```bash
npx skills remove scroll-world -g -a codex -y
```

## Other agents

### Claude Code plugin

```text
/plugin marketplace add bright-coder/scroll-world
/plugin install scroll-world@scroll-world
```

Then invoke `/scroll-world` or ask for a scroll-through landing page.

### Skills CLI interactive install

The Skills CLI supports Claude Code, Cursor, and many other compatible agents. Omit the
Codex target to choose interactively:

```bash
npx skills add bright-coder/scroll-world
```

### Manual copy

```bash
git clone https://github.com/bright-coder/scroll-world.git
cp -R scroll-world/skills/scroll-world ~/.claude/skills/   # Claude Code
cp -R scroll-world/skills/scroll-world ~/.codex/skills/    # Codex
```

## Troubleshooting

- **`ffmpeg` or `ffprobe` not found:** install FFmpeg, then restart the terminal and
  Codex so the updated `PATH` is loaded.
- **Python reports `CERTIFICATE_VERIFY_FAILED`:** install or select a Python runtime with
  a current CA certificate bundle. Do not disable TLS verification.
- **Kie frame upload returns HTTP 403:** confirm the same key and account in the Kie
  dashboard, then contact Kie support if the upload service still rejects it. Do not
  repeatedly submit replacement paid tasks.
- **Codex cannot see `$scroll-world`:** run `npx skills list -g -a codex`, reinstall the
  skill if it is missing, and restart Codex.
- **A Kie submission is marked uncertain:** keep its manifest and reconcile the Kie task
  history or contact support before authorizing any replacement task.

## Requirements and providers

- Codex built-in `image_gen` is the default still-image workflow.
- Kie.ai `bytedance/seedance-2-fast` is the default video model.
- `ffmpeg` / `ffprobe` handle frame extraction, validation, and encoding.
- Python 3 runs the dependency-free Kie client. Pillow is optional for background
  knockout work.
- The [Higgsfield CLI](https://higgsfield.ai) remains an optional authenticated fallback.

### Provider matrix

| Role | Default | Fallback | Notes |
|---|---|---|---|
| Scene stills | Codex built-in `image_gen` | Higgsfield `gpt_image_2` | Copy approved stills into the project workspace and inspect every image. |
| Video dives + connectors | Kie.ai `bytedance/seedance-2-fast` | Higgsfield frame-locking models | Kie runs at 720p and supports both `--start-image` and `--end-image`. |
| Pipeline guide | `references/pipeline-kie.md` | `references/pipeline.md` | Do not mix video providers inside one chain. |

## What it does

By default it uses Codex's built-in `image_gen` for cohesive isometric diorama stills and
[Kie.ai](https://kie.ai) `bytedance/seedance-2-fast` for the camera flights. Higgsfield
remains a complete fallback. Only video workflows that can frame-lock both ends of a seam
are supported. The resulting clips are scrubbed
by scroll position — the same technique behind Apple's scroll-through product pages. The
camera genuinely moves; scroll only drives time. It's **framework-agnostic**: you get the
provider pipelines, the prompt templates, and a portable vanilla-JS scrub engine that
drops into plain HTML, Next.js, Vue, or a Python-served page — nothing assumes a stack.

When invoked, the skill:

1. **Interviews you** — the subject/industry + pitch, a brand kit (import from a URL, hand
   it over, or have it proposed), art direction, the ordered scenes the camera visits,
   whether you want the **mobile version** (a second chain rendered natively in 9:16
   portrait — composed for phones, not a crop of the landscape film), and the **budget** —
   render tiers and stills source shown with estimated credit costs, approved before
   anything generates.
2. **Generates the assets** — one still per scene, one "dive-in" camera
   clip per scene, and the **connector** clips that join consecutive scenes, generated
   from the actual rendered frames of their neighbours so every seam is frame-identical.
   Mobile opt-in renders a parallel portrait chain the same way, frame-locked against its
   own 9:16 renders.
3. **Wires it up** — a config-driven scroll engine that plays the whole chain as one
   flight, serving the portrait clips and posters automatically on phones.

## What's in the skill

```
skills/scroll-world/
├── SKILL.md                    the procedure + the seam rule + gotchas
└── references/
    ├── prompts.md              intake checklist + still/video prompt templates
    ├── pipeline-kie.md         default Codex stills + Kie video workflow
    ├── pipeline.md             Higgsfield fallback workflow
    ├── scrub-engine.js         portable, config-driven scrub engine (blob-seek, lazy load, seam crossfade)
    ├── index-template.html     a minimal standalone page that mounts the engine
    └── knockout.py             background knockout for floating scenes
```

## Notes

- Video generation costs provider credits (~2N-1 video generations for N scenes; the
  native mobile chain doubles them) and takes a while, so the skill obtains budget
  approval, runs generations detached, and preserves resumable Kie manifests. Codex
  stills use the user's Codex allowance; the Higgsfield fallback has its own credit cost.
- The generated `.mp4`/`.webp` assets are produced per project; they're not shipped here.

## License

MIT — see [LICENSE](LICENSE).
