# Blue Ocean Marketing Studio

Makes faceless marketing videos for the Blue Ocean Marketing channel: script, voiceover,
captions, footage, finished MP4, thumbnail and upload text. Cost per video is about
1–2 cents of OpenAI usage; everything else is free.

| Step | Tool | Cost |
|---|---|---|
| Ideas and scripts | OpenAI (`gpt-5-mini`, low reasoning) | ~$0.003–0.02 per script |
| Voiceover + word timings | Microsoft Edge neural voices (edge-tts) | free |
| Stock footage | Pexels API | free |
| Captions, editing, thumbnail | FFmpeg + Pillow, on your PC | free |

## Setup (once)

1. Keys: put them in `.env` (see `.env.example`). The tool also reads `.env.txt`.
   - `OPENAI_API_KEY=...`
   - `PEXELS_API_KEY=...`: free at https://www.pexels.com/api/. Without it, videos use branded text cards.
2. Install:

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

FFmpeg must be on PATH (it already is on this PC).

## Daily workflow

```bash
# 1. Get a backlog of ideas -> projects/ideas.md
.venv\Scripts\python -m studio ideas --count 20

# 2. Write a script (long = YouTube, short = Shorts/TikTok/Reels)
.venv\Scripts\python -m studio script "Why a plumber's Google listing gets no calls" --format long

# 3. Edit projects/<folder>/script.json: add your own example, verify every [CHECK: ...]
#    fact and remove the marker. Rendering refuses to start while markers remain.

# 4. Render
.venv\Scripts\python -m studio render "projects/<folder>"
```

```bash
# 5. Turn the long video's step sections into vertical Shorts (no OpenAI cost)
.venv\Scripts\python -m studio shorts "projects/<folder>" --max 5
```

Shorts land in `projects/<folder>/shorts/short_01/` and onwards, each with its own video, thumbnail and
upload text. Use `--scripts-only` to review them before rendering.

`make "<topic>" --format short` does steps 2 and 4 in one go (it still stops on `[CHECK]` markers).

Each project folder ends up with `video.mp4`, `thumbnail.jpg`, `upload.txt` (titles,
description with chapters, footage credits, tags) and `script.md`.

Re-rendering after an edit is fast: scenes whose narration and footage query did not change
reuse their cached voice and clips.

## Publishing through Buffer

Needs `Buffer_API` (or `BUFFER_API_KEY`) and `CLOUDINARY_URL` in `.env.txt`. Buffer only accepts
videos by public URL, so each video is first uploaded to your Cloudinary account (free plan).

```bash
.venv\Scripts\python -m studio channels                              # connected accounts
.venv\Scripts\python -m studio publish "projects/<folder>" --dry-run  # preview, posts nothing
.venv\Scripts\python -m studio publish "projects/<folder>"            # save as Buffer drafts
.venv\Scripts\python -m studio publish "projects/<folder>" --queue    # Buffer posting schedule
.venv\Scripts\python -m studio publish "projects/<folder>" --at 2026-10-06T15:00
```

- Default is **drafts**: nothing goes live until you approve it in Buffer.
- Services come from `[publish] services` in `channel.toml` (YouTube and Facebook); override with `--services`.
- Shorts get `#Shorts` in the YouTube title and post as Facebook Reels.
- `publish.json` in each project records what was sent, so a video is never posted twice (`--again` overrides).
- Publishing refuses while any `[CHECK: ...]` fact markers remain in the script.
- You can pass several folders at once, e.g. `publish "<folder>/shorts/short_01" "<folder>/shorts/short_02"`.

## Weekly batch (fits the free plans)

1 long video + up to 7 Shorts (5 cut from the long video, 2 standalone) = 8 posts per channel,
under Buffer's free limit of 10. OpenAI cost about 3–5 cents a week.

```bash
.venv\Scripts\python -m studio week plan                      # writes 3 scripts for review
# edit each script.json: add your example, clear every [CHECK: ...]
.venv\Scripts\python -m studio week build "projects/weeks/<date>.json"
.venv\Scripts\python -m studio week schedule "projects/weeks/<date>.json"             # dated drafts
.venv\Scripts\python -m studio week schedule "projects/weeks/<date>.json" --schedule  # really schedule
```

Schedule: long video Monday 17:00, then one Short a day at 18:00 (change with `--start`,
`--long-time`, `--short-time`). `--schedule` refuses if a channel would go over 10 scheduled posts.

## Free-plan housekeeping

```bash
.venv\Scripts\python -m studio cleanup     # deletes hosted videos Buffer has finished posting
```

Run it once a week. Videos still in drafts, scheduled or failed are kept.

## Settings

Everything about the channel lives in `channel.toml`: niche, call to action, brand colors,
fonts, voice, video length, model. To use OpenAI's voice instead of the free one, set
`provider = "openai"` under `[voice]` (about $0.015 per minute of audio).

## Tests

```bash
.venv\Scripts\python -m pytest -q
```
