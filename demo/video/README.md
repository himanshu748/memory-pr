# The demo video

`capture.mjs` films the two-minute demo in one scripted pass and writes a silent 1920x1080 MP4.
Nothing is recorded from a person: it is a screen capture of the title cards in `cards.html`,
two pull requests on GitHub seen logged out, and the live page, with the captions in
`captions.json` drawn on top. No sound, no outside images, fonts or footage. The type is the
page's own Geist, from `site/vendor/fonts/`.

```
cd demo/video
npm install
node capture.mjs        # writes out/memory-pr-demo.mp4 (not committed)
```

It needs Google Chrome and `ffmpeg`. To drive a Chrome that is already running, set
`CDP_URL` (for example `http://localhost:9222`); a fresh private context is used either way, so
GitHub is always logged out and in the light theme.

| Setting | Default | What it is |
| --- | --- | --- |
| `SITE` | `https://memory-pr.vercel.app/` | the page to film; `http://localhost:8000/` works with `python -m http.server -d site` |
| `BLOCKED` | `15` | a pull request the memory review blocks |
| `PASSING` | `6` | one it passes |
| `OUT` | `out` | where the frames and the MP4 go |

How it works: Chrome's screencast sends a frame each time the page changes, with its time.
Frames that arrive while a page loads are dropped and their time is cut, so the video never
shows a loading state. `ffmpeg` turns the timed frames into 30 frames a second. The script
fails if the result is longer than two minutes.

On GitHub the capture hides profile pictures, the site header with its sign-in buttons, and
timeline entries that are not from the review bot. The merge box is not shown to logged-out
visitors, so the video shows the failed required check on the Checks tab instead.

The numbers on the measured-result card are the README's "Measured result" table. Change them
there and in `cards.html` together.
