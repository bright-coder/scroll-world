---
name: scroll-world
description: >
  Build an immersive scroll-scrubbed "fly through the world" landing page for any
  industry or brand using Codex image generation and Kie.ai video by default, with a
  Higgsfield fallback. As the visitor scrolls, a pre-rendered camera
  flies from outside each scene into its interior, then flows on to the next scene
  with NO cuts — one continuous connected flight (Emons-style isometric diorama world,
  or any art direction you pick). The skill interviews the user for the topic, the
  story beats/sections, and brand kit, then generates cohesive scenes + seamless camera
  clips and wires a portable, framework-agnostic scroll-scrub engine.
  Use when the user wants a "3D world" / "browse-through-the-industry" hero, a scroll
  cinematic, a diorama landing, or to turn a business into a scrollable world.
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion, Skill
---

# scroll-world

Produces a landing page where **scroll drives a camera**: it dives from outside a scene
into its interior, then flies out and into the next scene, continuously, with no visible
cuts. The visuals are AI-generated; the page just scrubs pre-rendered video
by scroll position. This is the same technique behind Apple's scroll-through product
pages — the camera genuinely moves, scroll only drives time.

**What you generate:** N scene stills → N "dive-in" camera clips → N-1 "connector" clips
that join consecutive scenes seamlessly → a portable scrub engine that plays the whole
chain as one flight.

**The one rule that makes or breaks it:** seams must be *frame-identical*. Read
[The seamless chain](#step-5--the-seamless-chain-the-critical-part) before generating any
connector. Getting this wrong is the single most common failure and produces a visible
"pop" between scenes.

Do not assume a frontend framework. The scrub engine in `references/scrub-engine.js` is
self-contained vanilla JS (it builds its own DOM + injects its own CSS into a container
you give it), so it drops into plain HTML, Next.js, Vue, a Python-served page, anything.
The value of this skill is the provider pipelines, the prompts, and the seam method —
not the framework.

---

## Step 0 — Bootstrap

1. **Select the default route.** Unless the user explicitly chose the fallback, use:

   ```bash
   STILLS_SOURCE=codex
   MEDIA_PROVIDER=kie
   KIE_MODEL=${KIE_MODEL:-bytedance/seedance-2-fast}
   ```

   Codex default stills: built-in `image_gen`, copied into the project workspace.
   Default video provider: Kie.ai `bytedance/seedance-2-fast`.
   Fallback video provider: Higgsfield [`pipeline.md`](references/pipeline.md).
   An existing user-approved `KIE_MODEL` environment override is authoritative; do not
   silently replace it with the default.
2. **Kie credential and credits.** Source `KIE_API_KEY` from the project's ignored
   `.env.local` (never pass it as an argument, print it, or commit it). Use the bundled
   `scripts/kie_client.py`; it needs Python 3 and no third-party package. A desktop
   architecture-B run is `N + (N-1)` paid videos, and native mobile doubles that. State
   the estimate with re-roll headroom and obtain approval before any submission.
3. **ffmpeg / ffprobe** on `$PATH` (canvas normalization, rendered-frame extraction,
   download validation, audio removal, and scrub-friendly encoding).
4. **An image tool** for background knockout if you want floating scenes: PIL
   (`python3 -c "import PIL"`), or `cwebp`/`sips`. Optional — see Step 3.
5. **Higgsfield fallback only.** If Kie is unavailable or the user chooses Higgsfield,
   follow `references/pipeline.md` end-to-end. Do not mix providers within a video chain.
   Authenticate the CLI and confirm its credits before falling back.
6. Caveats: macOS ships **bash 3.2** (no `declare -A`); don't use associative arrays.
   Video generations take minutes — always run them detached and poll. The Kie client
   accepts local files, uploads them to temporary URLs, persists an output-sidecar
   manifest, and downloads the result. Preserve that manifest and use `wait --manifest`
   after a timeout or interruption; never resubmit blindly and risk duplicate spend.

---

## Step 1 — Interview the user

The **subject is the user's to state — ask it as an open question in plain prose**, never a
fabricated multiple-choice. A made-up list of industries biases them and reads as you
deciding their business for them; let them answer in their own words (their real business,
a client's, or any idea). Reserve structured multiple-choice (`AskUserQuestion` in Claude
Code; a plain either/or question elsewhere) for the genuinely
enumerable, lower-stakes choices below — art direction and brand-kit approach — and even
there, signal they can go their own way ("Other"). Ask only what you can't sensibly
default. Cover:

1. **Subject** (ask openly, not multiple-choice) — "What should this world be about? Your
   business, a client's, or any idea — a word or a sentence is fine." Capture the
   industry/product + a one-line pitch (e.g. "a bubble tea company, from leaf to last
   sip"), and a brand name if they have one; otherwise you'll propose one below.
2. **Brand kit** — offer three paths, pick one:
   - Import from a URL: `higgsfield marketing-studio brand-kits fetch --url <site> --wait`
     (pulls name, colours, tone). Then read it back with `brand-kits list --json`.
   - The user hands you palette + name + tone directly.
   - You propose a palette + name and let them approve.
   Capture **4–6 named hex values**, a display name, and a tone word or two.
3. **Art direction** — default is "soft matte low-poly **clay diorama**, isometric,
   tilt-shift miniature, warm light." Offer alternatives (flat papercraft, glossy toy,
   claymation, neon night). Whatever is chosen becomes the shared **style preamble**
   reused verbatim in every scene prompt (this is what makes the world cohesive).
4. **The journey (sections)** — the ordered scenes the camera flies through. Propose a
   set derived from the subject's own value chain and let the user edit. 5–7 works well.
   Boba example: farms → pearl kitchen → flagship shop → delivery → community plaza →
   the hero product. Each section needs: a short subject description (what's IN the
   diorama), an eyebrow, a headline, one line of body, and 0–3 tag pills. The last
   section is usually the hero product + the CTA.
5. **Mobile version — ALWAYS ask this; never silently generate both.** Ask as a
   two-option choice (`AskUserQuestion` in Claude Code; a plain question elsewhere):
   *"Want a mobile-optimized version too? The mobile version is a second camera chain
   rendered natively in **9:16 portrait** — composed for phones, not a crop of the
   landscape film — which roughly doubles the video-provider credit spend (state the
   estimated number)."*
   Options: "Desktop only" / "Desktop + mobile (native 9:16 — ~2× credits)". The
   credit cost must be stated to the user, not just implied.
   What the answer gates:
   - **Yes** → render the parallel 9:16 portrait chain and ship it as the mobile variants
     (Step 6 / pipeline-kie.md §6): portrait start canvases → 9:16 dives + connectors
     frame-locked against their own renders → 720-wide `-m.mp4` encodes → `stillMobile`
     portrait posters. Wire `clipMobile`/`connectorsMobile`/`stillMobile` (Step 7); run
     the full mobile QA (Step 8). Budget ~2N-1 extra video gens + NSFW re-rolls.
     **Never ship the centre-crop as the mobile version by default** — if credits can't
     cover the portrait chain, say so and offer crop encodes as an
     explicitly-labelled stopgap the user must approve.
   - **No** → skip the mobile encodes and wiring entirely. The engine's phone hardening
     (seek-coalescing, iOS priming, safe-area CSS) is always on regardless — that's not
     a "mobile version," it's just the page not breaking when a phone visits — so a
     desktop-only build still degrades gracefully.

6. **Budget — decide before anything renders.** Default to Codex stills plus Kie video,
   then compute and state the user's total: Codex allowance for `N` stills;
   `N + (N-1)` paid Kie videos for architecture B or `N` for architecture A; double the
   video count if native mobile is approved; add ~15% re-roll headroom. Pricing and plans
   change, so use the user's current provider estimate rather than inventing a per-clip
   price. Warn whenever the estimate exceeds ~70% of the available credit balance, and
   get an explicit go before generating.
   - **Default video model:** `bytedance/seedance-2-fast` at 720p through Kie. Respect an
     already approved `KIE_MODEL` override. If the user proposes another override, confirm
     it accepts both first and last frames before using architecture B.
   - **Default stills source:** Codex's built-in `image_gen`, charged to the user's Codex
     allowance. Copy every output into the project workspace. Use one source and one
     byte-identical style preamble for all stills; mixing generators reads as style drift.
   - **Fallback:** Higgsfield remains available with its own still/video credits and model
     roster. If selected, use `references/pipeline.md` and its calibration instructions
     for the whole chain.

If the user names a video model outside the documented defaults, honor it **only if it
can frame-lock the required seams** (Step 4). This skill only ships seamless output, so
decline a model that cannot accept the needed rendered boundary frames.

Keep the scroll mechanic fixed (continuous fly-through) — that's the point of the skill.
See `references/prompts.md` for the intake checklist and copy structure.

---

## Step 2 — Generate the scene stills

One image per section, **all sharing the same style preamble** for cohesion. With the
default `STILLS_SOURCE=codex`, invoke Codex's built-in `image_gen` tool directly. Do not
launch a nested Codex CLI process. After each generation, copy the resulting PNG from the
tool's output location into the project workspace under `$WORK`, where the video pipeline
can read it.

Prompt shape (full templates in `references/prompts.md`):

```
<STYLE PREAMBLE, identical every time>. On a plain solid <bg> background with a soft
contact shadow. <PALETTE hexes>. No text, no letters, no logos, centered.
Subject: <what is in THIS diorama>.
```

- **Inspect every generated still before continuing.** Open each image at full size and
  check subject, composition, palette, camera angle, lighting, legibility, accidental
  text, and shared-world cohesion. Re-generate any failure before spending video credits.
- **Normalize every approved still to the target video canvas.** Landscape stills become
  exact 16:9 canvases and mobile stills become exact 9:16 canvases. Use FFmpeg scaling +
  padding (or an intentional crop approved by the user), then inspect the normalized PNG
  again. Never pass an arbitrary 3:2 output straight into a 16:9 video task.
- Copy landscape inputs as `$WORK/start_<name>.png`. If native mobile was approved, make
  separately composed portrait art and copy its normalized input as
  `$WORK/start-mobile_<name>.png`; do not center-crop landscape art and call it mobile.
- If image generation fails, re-run only that still. One source for the whole build and
  the byte-identical style preamble are what keep the world cohesive.
- With `STILLS_SOURCE=higgsfield`, follow the still-generation section in the fallback
  `references/pipeline.md`, then apply the same inspection and canvas-normalization gates.

See `references/pipeline-kie.md` for the default copy, normalization, and video commands.

---

## Step 3 — (Optional) Float the scenes

If you want the dioramas to float over an atmospheric background instead of sitting in a
solid box, knock out the flat background to transparency with
`references/knockout.py` (border-connected flood fill — preserves interior colour that
matches the bg, e.g. cream walls). Then encode to webp. If you'd rather keep it simple,
just make the page background the same colour as the scene background and skip this.

These stills double as **video posters and lazy-load fallbacks**, so keep them.

---

## Step 4 — Camera architecture (pick one — this makes or breaks the feel)

How the camera moves *between* scenes is the single biggest quality lever. Two shapes;
pick by aesthetic.

### Video model — pick ONE for the whole chain

**This skill only ships seamless output.** Every chained clip must accept a rendered
`--start-image`, and architecture-B connectors must also accept a rendered
`--end-image`. Conditioning on a loose reference image is not frame locking.

| Route | Model | Start/end image | Notes |
|---|---|---|---|
| Default | Kie.ai `bytedance/seedance-2-fast` | ✓ / ✓ | 720p, 16:9 or 9:16, 15-second default; `scripts/kie_client.py` disables audio and persists a resumable manifest. |
| Approved override | `$KIE_MODEL` | Verify ✓ / ✓ for architecture B | Preserve an already approved override; never silently reset it. |
| Fallback | Higgsfield roster in `pipeline.md` | Model-dependent | Follow the fallback pipeline's exact per-model flags. |

Rules:
- **One provider and one model for all chained clips.** Renderers have distinct motion,
  color, and grain; mixing them mid-chain creates a character shift even if endpoint
  pixels match.
- Default to Kie.ai `bytedance/seedance-2-fast`. Use Higgsfield only when explicitly
  selected or when Kie cannot be used, and then follow `references/pipeline.md` for the
  whole asset run.
- `references/pipeline-kie.md` is the executable default. It uses local frame paths,
  720p, the exact `generate-video` / `wait --manifest` interface, and the approved
  `KIE_MODEL` environment override.

### A) Continuous forward take — RECOMMENDED for grounded / realistic / walkthrough
One camera that only ever glides **forward**, first scene through last, as a single take.
Generate the legs **sequentially**: leg 0 from scene-0's still (glide forward into it);
then each leg's `--start-image` = the **previous leg's ACTUAL last frame** (extract with
ffmpeg), prompt *"continue gliding smoothly FORWARD into [scene i], never pulling back"*
(or an expressive mid-leg move under the motion-handoff contract — see **Camera grammar**
below), and **no `--end-image`** — an end-image of a wide establishing shot forces the
camera to pull back, which is the #1 cause of stutter. Extract each leg's last frame to feed the
next. Result: every seam is frame-identical **and** the camera never reverses. There are
**no connectors** (skip Step 5) — the legs ARE the journey. Wire each leg as a section
clip with `connectors: []` and a small `crossfade` (~0.08). Even without an `--end-image`
the legs still arrive at distinct rooms (the prompt steers the content). Cost: strictly
**sequential** (can't parallelize) and slower; interiors trip the NSFW filter, so build in
re-rolls (3 attempts/leg).

### B) Dive-in + aerial connector — only for diorama / miniature / god's-eye worlds
A "dive into each scene" clip + a connector that pulls **up and out** and flies over to the
next scene (Step 5). The pull-out **reverses camera direction at every seam** (forward dive
→ backward pull-out). In a miniature/diorama world that reads as an intentional "zoom out
to the map, fly to the next island"; in a grounded first-person walkthrough it reads as a
jarring **rewind/stutter**. Use B only for the map-like aesthetic. When in doubt, use A.

### Camera grammar — the move should fit the concept (A is NOT "forward only")

"Forward only" is the *seam* rule, not the *leg* rule. The physics of the chain:

- **Position continuity** at a seam comes from the frame handoff (next leg starts from the
  previous leg's actual last frame).
- **Velocity continuity** at a seam means the camera must never *reverse across a seam* —
  that's the rewind stutter.
- **Inside a single leg the camera is free.** One leg is one continuous render — there is
  no seam to break mid-leg, so orbits, crane-ups, lateral tracking, even a push-in that
  eases back out are all safe *within* the clip. Reversals are only fatal *across* seams.

So give each leg an expressive move chosen from the scene's own logic, under a **motion
handoff contract**: every leg **ends by settling into a slow, steady forward drift** toward
the next destination (final ~1 s), and every leg **begins by continuing that same drift**.
Keep both clauses in the prompts verbatim (templates in `references/prompts.md`).

Pick the grammar from the concept:

| Concept / tone | Mid-leg move |
|---|---|
| Product / luxury retail | slow half-orbit around the hero object, then continue past it |
| Real estate / hospitality | steadicam glide through doorways; gentle crane-up in atria |
| Industrial / process / logistics | low lateral track alongside the line, foreground parallax |
| Travel / outdoors / campus | drone-style rise-and-reveal, then a descending swoop |
| Food / craft / detail-driven | push in close to the craft moment, ease back, carry on |
| Playful miniature (arch. B) | dives + aerial hops — the connector IS the grammar |

Honest costs: expressive mid-leg moves raise re-roll odds — the model can end a fancy move
in a state that isn't a clean forward drift. Mitigations: keep the final-second settle
clause verbatim; **eyeball each leg's last frame before chaining the next** (it should look
like a frame from a gentle forward glide — if not, re-roll before wasting the next leg);
budget ~1 extra re-roll per expressive leg. A plain forward glide stays the zero-risk
default — use it for legs where the scene itself is the show.

Two related pacing knobs live in the engine (Step 7): per-section `scroll` (more scroll
distance = longer dwell in that scene) and `linger` (the camera settles mid-scene exactly
while the copy peaks, then picks up speed toward the seam). Prefer expressive motion in the
*clip* and restraint in the *scrub mapping* — they compound.

And remember scroll is a scrubber: visitors can scroll **up**, so every move also plays in
reverse. That's free and expected — no extra work — but it's another reason seam velocity
must be consistent in both directions (a seam that reads fine forward reads as a stutter
backward too if velocity flips).

**For B**, one camera flight per scene: starts high/outside, descends into the interior,
structure opens. Model: the chain model you picked above (default Kie.ai
**`bytedance/seedance-2-fast`**), with `--start-image` set to the normalized 16:9 scene
canvas.

- Use the **solid-background still** (not the knocked-out transparent one) as the
  start image, so the video has a full frame.
- Prompt: "Single continuous cinematic camera move, no cuts. Begin high and far looking
  at the whole <scene> from outside … descend and fly inside toward <focal point> … the
  roof/walls gently open to reveal the interior. <style>, smooth graceful slow motion.
  No text." (Template in `references/prompts.md`.)
- Default Kie params are `--aspect-ratio 16:9 --resolution 720p --duration 15`; the client
  sets audio off, downloads and validates the result, and saves
  `<output>.kie.json`. Run concurrently with bounded parallelism and detached execution.
  Resume an interrupted task with `wait --manifest`; never resubmit blindly.
- Keep the downloaded 720p sources — their actual rendered frames are the only valid seam
  inputs. Higgsfield fallback flags remain in `references/pipeline.md`.

---

## Step 5 — Connectors (architecture B only)

Skip this whole step for architecture **A** — the forward take has no connectors; its legs
already chain seamlessly. This step applies to **B** (diorama/miniature), and note the
reversal caveat from Step 4.

The connector clips are what make the world feel *connected* instead of cut. A connector
flies from the end of scene i out and into the start of scene i+1. **Both of its
endpoints must be the ACTUAL RENDERED FRAMES of the neighbouring clips — never the
original diorama still.**

Why: every video generation renders slightly differently. If a connector *ends* on
a fresh render of "the kitchen diorama," but the next dive clip *starts* on its own
different render of that same diorama, the two won't match and you get a pop at the seam.
The fix is to hand off the exact pixels:

```
For each connector between dive_i and dive_{i+1}:
  start-image = the LAST frame extracted from dive_i's rendered video
  end-image   = the FIRST frame extracted from dive_{i+1}'s rendered video
```

Now every seam is frame-identical on *both* sides:
`dive_i.end == connector.start` and `connector.end == dive_{i+1}.start`.

Extract the boundary frames from the rendered dives (not the stills):

```bash
ffmpeg -sseof -0.15 -i dive_i.mp4   -frames:v 1 -q:v 2 dive_i_last.png    # interior of i
ffmpeg -ss 0      -i dive_{i+1}.mp4 -frames:v 1 -q:v 2 dive_next_first.png # establishing of i+1
```

Generate the connector with the same model as the dives. Connectors need `--end-image`,
so the selected model must accept it. The default executable command is:

```bash
python3 "$SKILL/scripts/kie_client.py" generate-video \
  --prompt-file "$WORK/conn_$index.txt" \
  --start-image "$WORK/last_$previous.png" \
  --end-image "$WORK/first_$next.png" \
  --aspect-ratio 16:9 --resolution 720p --duration 15 \
  --output "$WORK/conn_$index.mp4"
```

Run it detached. On timeout or interruption, resume the output sidecar with
`python3 "$SKILL/scripts/kie_client.py" wait --manifest "$WORK/conn_$index.mp4.kie.json" --output "$WORK/conn_$index.mp4"`.

Connector prompt: "Single continuous camera move, no cuts. Pull up and back out of
<scene i>, rise into the sky, glide across the connected miniature world, and arrive
above <scene i+1>, beginning to descend toward it. Seamless flowing aerial transition.
<style>. No text." (Template in `references/prompts.md`.)

Insurance: the model may land *close* to the end-image rather than pixel-perfect, so the
engine still applies a **short crossfade** (a few frames) at each seam. Frame-matched
endpoints + a small crossfade = no visible cut. Never skip the actual-frame handoff and
rely on the crossfade alone; a big content jump can't be hidden by a crossfade.

---

## Step 6 — Encode for smooth scrubbing

Scrubbing = setting `video.currentTime` from scroll. Two things matter, and they are
often gotten wrong:

1. **Seekability, not keyframe density, is what makes scrubbing work.** Many static
   hosts (and `python -m http.server`) don't serve HTTP byte-range requests, which pins
   `video.seekable` to `[0,0]` and clamps *every* seek to frame 0 — the video looks
   frozen. The robust fix is to **fetch each clip as a `Blob` and play it from an
   in-memory object URL** (blobs are always fully seekable). The engine does this.
   Because of it, you do **not** need all-intra video.
2. **Don't shrink quality to get smooth seeks.** Encode at the **native resolution**
   (720p from the default Kie model; fallback providers may differ), `crf ~20`, a
   **small GOP** (`-g 8`) rather
   than all-intra (all-intra bloats an 8s clip to ~25 MB; GOP 8 is ~8 MB and scrubs
   fine via blob). Strip audio, add faststart, and a light `unsharp` counters video
   softness:

```bash
ffmpeg -i src.mp4 -an -vf "unsharp=5:5:0.8:5:5:0.0" \
  -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p \
  -g 8 -keyint_min 8 -sc_threshold 0 -movflags +faststart out.mp4
```

Encode all 2N-1 clips (dives + connectors) with the same settings for uniform quality.

**Mobile encodes (only if the user opted in at Step 1.5).** The mobile version is
the **native 9:16 portrait chain** (`pipeline-kie.md` §6): portrait renders of every dive and
connector, encoded **720 wide (`scale=720:-2`), `-g 4`** (more keyframes = cheaper seeks —
phone decoders' seek cost scales with GOP length), crf 23 — wired as `clipMobile` /
`connectorsMobile`, with each portrait dive's first frame extracted as the section's
`stillMobile` poster (Step 7). The engine serves them automatically on phones and falls
back to the desktop clip when absent. A 16:9 centre-crop encode is a **fallback only** —
for when credits cannot cover the portrait chain — and shipping it must be called out to
the user, never silent. If the user chose
desktop-only, skip this — the engine still hardens phone scrubbing regardless
(seek-coalescing, iOS priming), so the page degrades gracefully rather than breaking.

---

## Step 7 — Assemble the page

Copy `references/scrub-engine.js` (and, if you want a fully standalone page, the tiny
`references/index-template.html`) into the user's project — or adapt into their
framework. It's config-driven and self-contained:

```js
mountScrollWorld(document.getElementById('world'), {
  brand: { name: 'Pearl & Co.' },
  diveScroll: 1.3, connScroll: 0.9,          // viewport-heights of scroll per clip
  sections: [
    { id:'farm', label:'The Farms', still:'assets/farm.webp',
      clip:'assets/vid/farm.mp4',
      clipMobile:'assets/vid/farm-m.mp4',      // mobile opt-in only: native 9:16 render
      stillMobile:'assets/farm-m.webp',        // its first frame as the portrait poster
      scroll: 1.6, linger: 0.45,   // optional pacing: longer dwell + camera settles mid-scene
      accent:'#8FB98A', eyebrow:'From leaf to last sip', title:'It starts in the hills.',
      body:'…', tags:['Single-origin','Hand-picked'] },
    // …one per section; last may carry a `cta`
  ],
  connectors:       ['assets/vid/conn1.mp4','assets/vid/conn2.mp4',   /* … length = sections-1 */],
  connectorsMobile: ['assets/vid/conn1-m.mp4','assets/vid/conn2-m.mp4' /* … same length; mobile opt-in only */],
});
```

The engine handles: the ordered dive/connector chain, scroll→currentTime with rAF
smoothing, blob loading, lazy prefetch of nearby clips, frame-matched crossfades, pinned
per-section copy (first section greets on landing, last holds its CTA), a route rail,
`prefers-reduced-motion`, and mobile. **Pacing per section:** `scroll` overrides
`diveScroll` for that scene (more scroll = longer dwell) and `linger` (0–1, keep ≤ 0.6)
remaps time so the camera settles mid-scene — exactly while the copy peaks — then speeds
up toward the seam; seam frames are untouched (f(0)=0, f(1)=1). Give the hero and finale
scenes a higher `scroll` + some `linger`; keep transit scenes brisk. Theme it with CSS variables (`--accent`,
`--sw-bg`, `--sw-ink`, …) — the visual identity comes from the generated clips, so the
chrome stays quiet. See the header of `scrub-engine.js` for the full config + CSS vars.

**On phones the engine adapts automatically** (coarse pointer or ≤860px): it serves
`clipMobile` / `connectorsMobile` when present, **coalesces seeks** (never queues a new
`currentTime` while the decoder is still seeking — this is what stops a fast flick from
freezing the clip), **keeps the still as a poster until the clip paints its first frame**
and **primes each video on first touch** (fixes iOS's blank-until-played video), drops the
drifting particles, ignores URL-bar-only resizes (no scroll jump), and uses safe-area
insets so copy clears the notch/home indicator. All of this hardening is on by default —
no config needed. The `clipMobile`/`connectorsMobile` encodes are the opt-in part
(Step 1.5): only wire them when the user asked for the mobile version.

For non-JS backends (Python/Rails/etc.): serve the assets and drop the engine `<script>`
into the rendered HTML; nothing about it is framework-specific.

---

## Step 8 — QA the seams (don't skip)

Drive the page in a headless browser and **verify frame continuity at the seams**, which
is the thing most likely to be wrong:

- Screenshot at scroll positions just before and just after each seam. The two frames
  must be near-identical (the dive's last frame == the connector's first frame). If they
  pop, you used the diorama still instead of the actual rendered frame (redo Step 5), or
  the crossfade band is too short.
- Check the console for errors, confirm `video.seekable.end(0) > 0` (blob working), and
  that `currentTime` tracks scroll across each clip's band.
- **Mobile — full checklist only if the user opted into the mobile version (Step 1.5).**
  For a desktop-only build, just sanity-check a phone viewport once: page loads, still
  posters show, nothing overlaps — the engine's hardening covers graceful degradation.
  For the mobile build (do this on a real phone or an emulated one, portrait + landscape):
  - Emulate a phone viewport **with CPU throttled 4–6×** and scroll fast — the clip should
    track without freezing (the seek-coalescing + `-m.mp4` encodes are what make this hold).
  - Confirm the first scene shows immediately (its still is the poster) and the video takes
    over the instant you scroll — no blank/black scene (the iOS priming fix). Test iOS Safari
    specifically; it's the one that goes blank if this regresses.
  - Verify the `-m.mp4` variant is actually served on mobile (Network panel), and the
    native landscape master on desktop. The mobile clips must be **natively portrait**
    (`videoWidth < videoHeight` — not a downscaled 16:9 file), and the `stillMobile`
    posters must be served and match each portrait clip's first frame (no
    landscape→portrait flash when the video paints).
  - Slowly scroll so the URL bar collapses — the page must **not jump** (height-only resizes
    are ignored on touch). Rotate the device — layout should recompose cleanly.
  - Only if the crop **fallback** shipped (no credits for the portrait chain): portrait
    crops a 16:9 clip to its centre — confirm the focal subject still reads, and remind
    the user this is the stopgap, not the mobile version.
- Check reduced-motion (should fall back to the stills, no video, no particles).

---

## Gotchas (hard-won)

- **Seam pop** → connector endpoints were the diorama stills, not the neighbouring
  clips' actual frames. Always extract real frames (Step 5).
- **Seam stutter / camera "jumps backward"** → even with frame-matched seams, if the
  camera *velocity reverses* (forward dive, then a connector that pulls back out) it
  reads as a rewind. This is inherent to architecture B. For any grounded walkthrough use
  architecture A (one continuous forward take — legs chained from actual last frames, no
  pull-back, no `--end-image`); see Step 4.
- **Frozen video / stuck at frame 0** → `seekable=[0,0]`; the host isn't serving byte
  ranges. Use blob URLs (engine does).
- **Huge files** → you used all-intra. Use `-g 8` + blob instead.
- **Soft / low quality** → you downscaled or over-compressed. Encode the provider's native
  resolution (720p for default Kie), crf ≤ 20, add `unsharp`. Video is inherently softer
  than the stills — keep the stills as the lite fallback for max fidelity.
- **Concurrent failures / credit errors** → verify the live provider balance and retry
  only the individual task when it is safe. If a Kie task already has a manifest, resume
  it with `wait --manifest`; do not create a duplicate paid task.
- **Higgsfield fallback NSFW false-positives (Seedance `status "nsfw"`)** → its content filter flags
  perfectly innocuous clips, especially **bedroom, pool, spa/wellness** contexts and
  trigger words like "bed", "pool", "waterfall", "wine", "swim". It's partly the prompt
  wording and partly the reference frames. Fixes, in order: (1) re-roll — it's often
  non-deterministic and passes on the 2nd–3rd try; (2) strip trigger words and add
  "empty, unoccupied, no people, no figures, architectural, tasteful"; (3) regenerate
  just that clip on **`kling3_0`** with the same start/end frames — a different
  provider's filter often passes what Seedance blocks. Expect a slight render-character
  shift on that one clip (each model has its own grain/motion feel); for a 5s connector
  behind a crossfade that usually beats option (4): set the connector slot to `null` —
  the engine crossfades that seam directly (optional connectors), so the page still
  completes. Budget extra credits/time for these re-rolls on interiors/real-estate content.
- **Dark / custom theme** → the engine wraps its default tokens in `@layer sw`, so a
  page-level `:root` / `.sw-root { --sw-bg; --sw-ink; --sw-accent; --sw-font-* }` block
  wins cleanly (no specificity hacks). `--sw-ink` is your primary **text/heading** colour;
  the **accent** fills the primary button and active nav. For a dark theme, set `--sw-bg`
  dark and `--sw-ink` light — the copy scrim and title shadow follow `--sw-bg` automatically.
- **Phone scrub stutters / freezes on a fast flick** → the landscape master is too heavy for a
  phone decoder and seeks pile up. Ship the `-m.mp4` mobile encodes (720p, `-g 4`) and wire
  `clipMobile`/`connectorsMobile` (Step 6/7). The engine already coalesces seeks; the lighter
  encode is the other half. Still choppy on a low-end device? Tighten GOP (`-g 2` / all-intra).
- **Blank / black scene on iOS (desktop was fine)** → an iOS Safari quirk: a muted video that
  was never played won't paint a seeked frame. The engine fixes this by keeping the still as a
  poster until the clip paints and priming each video on first touch — so **don't** hide the
  still on `loadedmetadata` or strip the `playsinline`/`muted` attributes if you adapt the
  engine into a framework.
- **Page jumps while scrolling on mobile** → something is re-running layout on the URL-bar
  show/hide `resize`. The engine ignores height-only resizes on touch; if you ported it, gate
  your resize handler on a width change (keep the `orientationchange` path for rotation).
- **Copy hidden behind the URL bar / notch on mobile** → use the engine's safe-area-aware
  bottom offset (`env(safe-area-inset-bottom)` + `dvh`); make sure the page's
  `<meta viewport>` includes `viewport-fit=cover` (the template does).
- **Portrait crops the scene** → a 16:9 clip on a tall phone shows only its centre — which
  is why the mobile version is the native 9:16 chain (Step 6), never the crop. If you're seeing
  this on a mobile build, either the crop fallback shipped (call it out to the user) or the
  9:16 encodes aren't actually being served (check `videoWidth < videoHeight`). Keeping each
  scene's focal subject centred (prompts.md) still matters for the desktop film itself.
- **Unexpected audio** → the Kie client requests no audio; still mute in HTML and use
  `-an` on every final encode. In the Higgsfield fallback, omit unsupported audio flags.
- **Higgsfield fallback Kling rejects your flags** → `kling3_0` has **no `--resolution` param** (don't pass
  one; encode at whatever native res ffprobe reports) and **sound defaults on** — pass
  `--sound off`. Duration default is 5; legs/dives want 10.
- **Seam pop only where you "saved credits"** → you swapped models mid-chain, or used a
  start-image-only model where a connector needs an `--end-image`. One model for the whole
  chain. In the Higgsfield fallback, `seedance_2_0_mini` is the cheap frame-locking tier.
  Any model with reference-only inputs cannot hold a seam at all (Step 4).
- **White-box scenes** → match the page background to the generated still or knock it out
  (Step 3).
- **bash 3.2** on macOS → no associative arrays in scripts.
- **Connector grabs the wrong scene's frames** (or errors on a frame that doesn't exist
  yet) → the array loop ran in **zsh** (macOS default interactive shell), where arrays are
  1-indexed, not bash's 0-indexed. Keep every array-driven chain step in a `#!/bin/bash`
  script run via `bash script.sh` — never inline array loops in the interactive shell.

## References

- `references/prompts.md` — the intake checklist, style-preamble pattern, and every
  prompt template (scene still, dive, connector) with fill-in slots.
- `references/pipeline-kie.md` — default Codex `image_gen` stills + Kie.ai
  `bytedance/seedance-2-fast` workflow (normalize → generate → extract rendered frames →
  connectors → resume → encode → native mobile chain).
- `references/pipeline.md` — Higgsfield fallback batch scripts, bash-3.2-safe. Use it
  end-to-end rather than mixing providers inside a chain.
- `references/scrub-engine.js` — the portable, config-driven scrub engine (builds DOM +
  injects CSS; blob-seek, lazy load, seam crossfade, copy, route rail, reduced-motion, and
  phone hardening: mobile encodes, seek-coalescing, iOS priming, safe-area, no-jump resize).
- `references/index-template.html` — a minimal standalone page that mounts the engine.
- `references/knockout.py` — border-connected background knockout for floating scenes.
