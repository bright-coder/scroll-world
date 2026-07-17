# Default pipeline: Codex stills + Kie.ai video

This is the default Scroll World asset pipeline. Codex creates the stills with its
built-in `image_gen` tool, and the checked-in Kie client turns local frames into
frame-locked video with `bytedance/seedance-2-fast`. Use
[`pipeline.md`](pipeline.md) only for the Higgsfield fallback.

## 0. Configure once

Kie generations consume credits. Tell the user the estimated number of paid video
generations (`N` dives plus `N-1` connectors, doubled for a native mobile chain), include
re-roll headroom, and obtain approval before submitting any task. Never print or commit
the API key.

Put the credential in the project's ignored `.env.local`:

```bash
KIE_API_KEY=replace-with-the-user-key
```

Then configure the run from the project root. `KIE_MODEL` remains an approved override;
leave it at the default unless the user explicitly selects another compatible Kie model.

```bash
PROJECT="$PWD"
SKILL=/absolute/path/to/skills/scroll-world
WORK="$PROJECT/.scroll-world-work"
ASSETS="$PROJECT/assets"
NAMES="farm kitchen shop delivery plaza finale"

set -a
. "$PROJECT/.env.local"
set +a

MEDIA_PROVIDER=kie
STILLS_SOURCE=codex
if [ -z "${KIE_MODEL:-}" ]; then
  KIE_MODEL=bytedance/seedance-2-fast
fi
export MEDIA_PROVIDER STILLS_SOURCE KIE_MODEL

mkdir -p "$WORK" "$ASSETS/vid"
```

The Kie client accepts only local prompt and image paths. It uploads the frame files,
submits one task, saves a resumable `<output>.kie.json` manifest immediately, polls,
downloads the result, and validates it with FFprobe. Kie's uploaded-frame and result
URLs are temporary: do not treat them as assets or save them in site configuration.
Keep the local PNGs, manifests, and downloaded MP4 files instead.

## 1. Generate and inspect stills with Codex

For every section, use Codex's built-in `image_gen` tool with the shared style preamble
from `prompts.md`. Generate the image in Codex's image output location, visually inspect
it, then copy the approved file into the project workspace as
`$WORK/still_<name>.png`. Inspect **every** image at full size for composition, palette,
camera angle, text artifacts, and style drift; re-generate failures before video spend.

Kie must receive an exact video-aspect canvas. Normalize every approved landscape still
to 16:9 rather than handing it a 3:2 image:

```bash
name=farm
ffmpeg -v error -y -i "$WORK/still_$name.png" \
  -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=white" \
  -frames:v 1 "$WORK/start_$name.png"
```

Use the approved background color instead of `white` when the art direction specifies
one. Inspect the normalized 16:9 canvas too; padding must look intentional and the focal
subject must remain readable.

## 2. Generate 16:9 dives

Write one prompt to `$WORK/dive_<name>.txt`. The exact command for a dive is:

```bash
name=farm
python3 "$SKILL/scripts/kie_client.py" generate-video \
  --prompt-file "$WORK/dive_$name.txt" \
  --start-image "$WORK/start_$name.png" \
  --aspect-ratio 16:9 --resolution 720p --duration 15 \
  --output "$WORK/dive_$name.mp4"
```

Generation can take minutes. Put the approved commands in a bash script and launch the
script with the host agent's detached/background execution facility. From a normal shell,
the equivalent is:

```bash
nohup bash "$WORK/generate-dives.sh" > "$WORK/generate-dives.log" 2>&1 < /dev/null &
echo $! > "$WORK/generate-dives.pid"
```

Poll the log and manifests instead of holding an interactive foreground call open. A
timeout or interruption does not require a second paid generation. Resume the saved task
with the output sidecar:

```bash
python3 "$SKILL/scripts/kie_client.py" wait \
  --manifest "$WORK/dive_$name.mp4.kie.json" \
  --output "$WORK/dive_$name.mp4" \
  --timeout-seconds 900
```

Do not run `generate-video` again while that task manifest exists: use `wait --manifest`
so the same server task completes without duplicate spend.

## 3. Extract rendered boundary frames

Every seam uses actual pixels from the rendered videos. Never generate a new connector
endpoint image and never substitute an original still. Extract the first and last frames
from every completed dive with FFmpeg:

```bash
for name in $NAMES; do
  ffmpeg -v error -y -ss 0 -i "$WORK/dive_$name.mp4" \
    -frames:v 1 -q:v 2 "$WORK/first_$name.png"
  ffmpeg -v error -y -sseof -0.15 -i "$WORK/dive_$name.mp4" \
    -frames:v 1 -q:v 2 "$WORK/last_$name.png"
done
```

Inspect the extracted boundaries. The connector between adjacent scenes must start at
`last_<previous>.png` and end at `first_<next>.png`.

## 4. Generate frame-locked connectors

Write one connector prompt to `$WORK/conn_<index>.txt`. For each adjacent pair, use the
rendered boundaries exactly:

```bash
index=1
previous=farm
next=kitchen
python3 "$SKILL/scripts/kie_client.py" generate-video \
  --prompt-file "$WORK/conn_$index.txt" \
  --start-image "$WORK/last_$previous.png" \
  --end-image "$WORK/first_$next.png" \
  --aspect-ratio 16:9 --resolution 720p --duration 15 \
  --output "$WORK/conn_$index.mp4"
```

Launch connector batches detached as above. Their manifests are also resumable, for
example:

```bash
python3 "$SKILL/scripts/kie_client.py" wait \
  --manifest "$WORK/conn_$index.mp4.kie.json" \
  --output "$WORK/conn_$index.mp4"
```

This preserves both seam identities:
`dive_previous.end == connector.start` and
`connector.end == dive_next.start`.

## 5. Remove audio and encode for scrubbing

Kie requests already set `generate_audio=false`, but strip any audio defensively. Encode
the native 720p picture as H.264 with a short GOP, faststart, and uniform pixel format so
scroll seeking is predictable:

```bash
encode_clip() {
  ffmpeg -v error -y -i "$1" -an \
    -vf "unsharp=5:5:0.8:5:5:0.0" \
    -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p \
    -g 8 -keyint_min 8 -sc_threshold 0 -movflags +faststart "$2"
}

for name in $NAMES; do
  encode_clip "$WORK/dive_$name.mp4" "$ASSETS/vid/$name.mp4"
done
for source in "$WORK"/conn_*.mp4; do
  index=${source##*/conn_}; index=${index%.mp4}
  encode_clip "$source" "$ASSETS/vid/conn$index.mp4"
done
```

## 6. Native 9:16 mobile chain (only when approved)

The mobile version is a separate portrait composition and paid video chain, not a crop
of the landscape film. Ask before generating it. Use `image_gen` to make or extend each
approved scene for a tall composition, visually inspect every result, copy it into the
workspace, then normalize it to a native 720×1280 canvas:

```bash
name=farm
ffmpeg -v error -y -i "$WORK/still-mobile_$name.png" \
  -vf "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=white" \
  -frames:v 1 "$WORK/start-mobile_$name.png"
```

Generate every portrait dive with `--aspect-ratio 9:16 --resolution 720p --duration 15`
and `--start-image "$WORK/start-mobile_$name.png"`:

```bash
python3 "$SKILL/scripts/kie_client.py" generate-video \
  --prompt-file "$WORK/dive-mobile_$name.txt" \
  --start-image "$WORK/start-mobile_$name.png" \
  --aspect-ratio 9:16 --resolution 720p --duration 15 \
  --output "$WORK/dive-mobile_$name.mp4"
```

Extract `first-mobile_*.png` and `last-mobile_*.png` from those **rendered portrait
videos**, then use them for the portrait seams:

```bash
index=1
previous=farm
next=kitchen
ffmpeg -v error -y -ss 0 -i "$WORK/dive-mobile_$name.mp4" \
  -frames:v 1 -q:v 2 "$WORK/first-mobile_$name.png"
ffmpeg -v error -y -sseof -0.15 -i "$WORK/dive-mobile_$name.mp4" \
  -frames:v 1 -q:v 2 "$WORK/last-mobile_$name.png"

python3 "$SKILL/scripts/kie_client.py" generate-video \
  --prompt-file "$WORK/conn-mobile_$index.txt" \
  --start-image "$WORK/last-mobile_$previous.png" \
  --end-image "$WORK/first-mobile_$next.png" \
  --aspect-ratio 9:16 --resolution 720p --duration 15 \
  --output "$WORK/conn-mobile_$index.mp4"
```

Never reuse landscape boundaries or newly generated seam art. Encode the portrait clips
at 720 pixels wide with audio removed and a shorter GOP:

```bash
ffmpeg -v error -y -i "$WORK/dive-mobile_$name.mp4" -an \
  -vf "scale=720:-2,unsharp=5:5:0.6:5:5:0.0" \
  -c:v libx264 -preset slow -crf 23 -pix_fmt yuv420p \
  -g 4 -keyint_min 4 -sc_threshold 0 -movflags +faststart \
  "$ASSETS/vid/$name-m.mp4"

ffmpeg -v error -y -i "$WORK/conn-mobile_$index.mp4" -an \
  -vf "scale=720:-2,unsharp=5:5:0.6:5:5:0.0" \
  -c:v libx264 -preset slow -crf 23 -pix_fmt yuv420p \
  -g 4 -keyint_min 4 -sc_threshold 0 -movflags +faststart \
  "$ASSETS/vid/conn$index-m.mp4"
```

Wire these clips as `clipMobile` and `connectorsMobile`; extract each portrait dive's
first rendered frame for its `stillMobile` poster.

If Kie cannot be used or the user explicitly chooses Higgsfield, follow
[`pipeline.md`](pipeline.md) as the fallback from start to finish; do not mix providers
inside one chain.
