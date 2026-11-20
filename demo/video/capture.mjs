// Captures the silent demo video: one scripted pass through the title cards, two real
// pull requests on GitHub (logged out) and the live page, with captions drawn on top.
//
//   npm install && node capture.mjs            # writes out/memory-pr-demo.mp4
//
// Needs Chrome and ffmpeg. Set CDP_URL to drive a Chrome that is already running
// (a fresh incognito context is used either way, so GitHub is always logged out).
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';

const here = dirname(fileURLToPath(import.meta.url));
const SITE = process.env.SITE || 'https://memory-pr.vercel.app/';
const BLOCKED = process.env.BLOCKED || '15'; // a pull request the memory review blocks
const PASSING = process.env.PASSING || '6'; // one it passes
const REPO = 'https://github.com/himanshu748/memory-pr';
const OUT = resolve(process.env.OUT || join(here, 'out')); // absolute: ffmpeg reads the frame list from inside it
const CARDS = pathToFileURL(join(here, 'cards.html')).href;
const captions = JSON.parse(readFileSync(join(here, 'captions.json'), 'utf8'));
// 1280x720 CSS pixels at 1.5x: text is large on a laptop and the frames are 1920x1080.
const VIEW = { width: 1280, height: 720 };
const SCALE = 1.5;
const FPS = 30;

const browser = process.env.CDP_URL
  ? await chromium.connectOverCDP(process.env.CDP_URL)
  : await chromium.launch({ channel: 'chrome' });
const context = await browser.newContext({ viewport: VIEW, deviceScaleFactor: SCALE, reducedMotion: 'no-preference', colorScheme: 'light' });
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
const sleep = (ms) => page.waitForTimeout(ms);

// ---------------------------------------------------------------- recording
// Chrome sends a frame whenever the page changes. Frames that arrive while `rolling`
// is false (page loads) are dropped and their time is cut out of the video.
rmSync(join(OUT, 'frames'), { recursive: true, force: true });
mkdirSync(join(OUT, 'frames'), { recursive: true });
const frames = [];
let rolling = false;
let cut = 0;
let pausedAt = null;
let last = null;
cdp.on('Page.screencastFrame', ({ data, metadata, sessionId }) => {
  cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {});
  last = { data, at: metadata.timestamp };
  if (rolling) keep(data, metadata.timestamp);
});
function keep(data, at) {
  const file = join(OUT, 'frames', `${String(frames.length).padStart(5, '0')}.png`);
  writeFileSync(file, Buffer.from(data, 'base64'));
  frames.push({ file, t: at - cut });
}
function roll() {
  if (pausedAt !== null) cut += Date.now() / 1000 - pausedAt;
  pausedAt = null;
  rolling = true;
  if (last) keep(last.data, Date.now() / 1000);
}
function pause() {
  rolling = false;
  pausedAt = Date.now() / 1000;
  if (last) keep(last.data, pausedAt);
}

// ---------------------------------------------------------------- what is drawn on top
// A caption bar, a pointer, a ring around what matters and a veil for scene changes.
// Everything is drawn here; no outside image, font or sound is used.
async function hud() {
  await page.evaluate(() => {
    if (document.getElementById('hud')) return;
    const el = (id, css) => {
      const d = document.createElement('div');
      d.id = id;
      d.style.cssText = css;
      document.documentElement.appendChild(d);
      return d;
    };
    const top = 'position:fixed;z-index:2147483646;pointer-events:none;';
    el('hud', 'display:none');
    const quiet = document.createElement('style');
    quiet.textContent = '::-webkit-scrollbar { display: none; }';
    document.head.appendChild(quiet);
    el('hud-veil', `${top}inset:0;background:#f2f0e9;opacity:1;transition:opacity .45s ease;z-index:2147483647`);
    el('hud-ring', `${top}border:3px solid #c8321e;border-radius:6px;opacity:0;transition:opacity .35s ease,left .5s ease,top .5s ease,width .5s ease,height .5s ease`);
    el('hud-caption', `${top}left:50%;bottom:26px;transform:translateX(-50%);max-width:1120px;width:max-content;padding:15px 28px;` +
      'background:#141412;border-radius:8px;color:#f2f0e9;text-align:center;' +
      'font:500 27px/1.3 Geist,"Helvetica Neue",Helvetica,Arial,-apple-system,"Segoe UI",Roboto,sans-serif;letter-spacing:-0.01em;opacity:0;transition:opacity .35s ease');
    const pointer = el('hud-pointer', `${top}left:640px;top:420px;width:26px;height:26px;transition:left .7s cubic-bezier(.3,.1,.2,1),top .7s cubic-bezier(.3,.1,.2,1);filter:drop-shadow(0 2px 5px #000c)`);
    pointer.innerHTML = '<svg viewBox="0 0 26 26" width="26" height="26"><path d="M3 2 L3 21 L8.5 16 L12 24 L15.5 22.5 L12 14.8 L19.5 14.8 Z" fill="#fff" stroke="#0a0e1a" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    el('hud-click', `${top}width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;border:3px solid #1f3fff;opacity:0`);
  });
}
const veil = async (on) => {
  await page.evaluate((o) => (document.getElementById('hud-veil').style.opacity = o ? 1 : 0), on);
  await sleep(500);
};
async function caption(key) {
  await page.evaluate((text) => {
    const c = document.getElementById('hud-caption');
    c.style.opacity = 0;
    if (text) setTimeout(() => ((c.textContent = text), (c.style.opacity = 1)), 250);
  }, key ? captions[key] : '');
  await sleep(key ? 600 : 350);
}
async function ring(target, pad = 8) {
  const box = target && (await page.locator(target).first().boundingBox());
  await page.evaluate(([b, p]) => {
    const r = document.getElementById('hud-ring');
    if (!b) return void (r.style.opacity = 0);
    Object.assign(r.style, { left: `${b.x - p}px`, top: `${b.y - p}px`, width: `${b.width + 2 * p}px`, height: `${b.height + 2 * p}px`, opacity: 1 });
  }, [box, pad]);
  await sleep(450);
}
async function point(target) {
  let b = await page.locator(target).first().boundingBox();
  if (b.y < 80 || b.y + b.height > 600) {
    // Bring it into the part of the frame the caption does not cover.
    await page.evaluate((dy) => scrollBy({ top: dy, behavior: 'smooth' }), b.y + b.height / 2 - 380);
    await sleep(800);
    b = await page.locator(target).first().boundingBox();
  }
  const [x, y] = [b.x + Math.min(b.width / 2, 150), b.y + b.height / 2];
  await page.evaluate(([px, py]) => Object.assign(document.getElementById('hud-pointer').style, { left: `${px}px`, top: `${py}px` }), [x, y]);
  await page.mouse.move(x, y, { steps: 12 });
  await sleep(650);
  return [x, y];
}
async function click(target) {
  const [x, y] = await point(target);
  await page.evaluate(([px, py]) => {
    const c = document.getElementById('hud-click');
    Object.assign(c.style, { left: `${px}px`, top: `${py}px` });
    c.animate([{ opacity: 0.9, transform: 'scale(.4)' }, { opacity: 0, transform: 'scale(1.5)' }], { duration: 450 });
  }, [x, y]);
  await sleep(180);
  await page.mouse.click(x, y);
}
/** Scroll so `target` sits `offset` pixels below the top of the frame. */
async function scrollTo(target, offset = 90) {
  await page.locator(target).first().evaluate((node, off) => {
    scrollTo({ top: node.getBoundingClientRect().top + scrollY - off, behavior: 'smooth' });
  }, offset);
  await sleep(950);
}
/** Load a page behind the veil; the wait is cut from the video. */
async function open(url, ready) {
  if (frames.length) await veil(true);
  pause();
  await page.goto(url, { waitUntil: 'networkidle' });
  if (ready) await ready();
  await hud();
  await sleep(400);
  roll();
  await veil(false);
}
async function card(id, ms) {
  await open(`${CARDS}#${id}`, async () => {
    await hud();
    await page.evaluate(() => (document.getElementById('hud-pointer').style.display = 'none'));
  });
  await sleep(ms);
}

// ---------------------------------------------------------------- the story
await cdp.send('Page.startScreencast', { format: 'png', everyNthFrame: 1 });

await card('title', 3000);
await card('problem', 5000);

// A blocked pull request on GitHub, logged out and in the light theme. Profile pictures,
// the site header with its sign-in buttons and anything asking to sign in are hidden:
// the story is the review comment and the check.
const github = () =>
  page.addStyleTag({
    content: `img[class*="avatar" i], .TimelineItem-avatar, .flash, .js-stale-session-flash, a[href*="/login"], a[href*="/signup"],
      .timeline-comment-actions, #partial-discussion-sidebar img, .discussion-timeline-actions,
      .js-timeline-item:not(:has(a.author[href="/apps/github-actions"])) { visibility: hidden !important; }
      .js-header-wrapper, header[role="banner"] { display: none !important; }`,
  });
const title = 'h1[class*="inlineTitle"]';
const review = '.timeline-comment:has(a.author:text-is("github-actions"))';
await open(`${REPO}/pull/${BLOCKED}`, github);
await ring(title);
await caption('pr_open');
await sleep(3000);
await ring(null);
await page.locator(review).last().evaluate((node) => scrollTo({ top: node.getBoundingClientRect().top + scrollY - 40, behavior: 'smooth' }));
await sleep(1100);
await caption('pr_comment');
await sleep(2600);
await page.locator(review).last().evaluate((node) => scrollBy({ top: Math.max(0, Math.min(node.offsetHeight - 430, 230)), behavior: 'smooth' }));
await sleep(3400);
// The checks box on the conversation tab does not load for logged-out visitors; the Checks tab does.
await open(`${REPO}/pull/${BLOCKED}/checks`, async () => {
  await github();
  await page.locator('summary', { hasText: 'memory-review' }).first().click();
  await page.waitForLoadState('networkidle');
  await github();
});
await caption('pr_check');
await sleep(3800);

// A correct pull request passes.
await open(`${REPO}/pull/${PASSING}`, github);
await page.locator(review).last().evaluate((node) => scrollTo({ top: node.getBoundingClientRect().top + scrollY - 40, behavior: 'instant' }));
await ring(`${review} >> nth=-1`, 4);
await caption('pr_pass');
await sleep(3600);

// The live page. It loads behind the veil, so the first screen's story is played again.
await open(SITE, () => page.waitForSelector('#engine.ok'));
await page.evaluate(() => {
  const card = document.querySelector('.proof');
  card.classList.add('still');
  void card.offsetWidth;
  card.classList.remove('still');
});
await caption('live_hero');
await sleep(4400);

// Write a memory: blocked, fixed in two clicks, merged.
await caption(null);
await scrollTo('#write', 62);
await caption('play_open');
await click('#starters button >> nth=0');
await sleep(3000);
await caption('play_fix1');
await click('#outcome [data-draft-fix] >> nth=0');
await sleep(3300);
await caption('play_fix2');
await click('#outcome [data-draft-fix] >> nth=0');
await sleep(2800);
await caption('play_merge');
await click('#outcome [data-merge-pull]');
await sleep(2700);

// The scripted pull requests: a stale clone that git merges cleanly.
await caption(null);
await scrollTo('#views', 62);
await click('#views button:has-text("Auditor")');
await scrollTo('.layout', 66);
await caption('live_stale');
await sleep(500);
await ring('#review .problems', 4);
await sleep(4400);
await ring(null);

// Cypher in the page, then a write that makes the review fail.
await click('#views button >> nth=0');
await caption(null);
await scrollTo('#console .hint', 74); // the query box and its result both fit above the caption
await caption('live_cypher');
await click('#examples button:has-text("What rests on the Postgres fact")');
await sleep(1300);
await ring('#result', 4);
await sleep(2800);
await ring(null);
await caption('live_break');
await click('#examples button:has-text("Break it")');
await sleep(500);
await scrollTo('#review', 76);
await ring('#review .problems', 4);
await sleep(3600);
await ring(null);

await card('relate', 5000);
await card('bench', 6600);
await card('end', 4500);
pause();
await cdp.send('Page.stopScreencast');
await context.close();
await browser.close();

// ---------------------------------------------------------------- frames to MP4
const end = frames.at(-1).t;
const list = frames
  .map((f, i) => `file '${f.file}'\nduration ${Math.max((frames[i + 1]?.t ?? end) - f.t, 0.001).toFixed(4)}`)
  .join('\n');
writeFileSync(join(OUT, 'frames.txt'), `${list}\nfile '${frames.at(-1).file}'\n`);
const mp4 = join(OUT, 'memory-pr-demo.mp4');
execFileSync('ffmpeg', [
  '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', join(OUT, 'frames.txt'),
  '-vf', `fps=${FPS},scale=1920:1080:flags=lanczos,format=yuv420p`,
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-movflags', '+faststart', '-an', mp4,
], { stdio: 'inherit' });
const seconds = (end - frames[0].t).toFixed(1);
console.log(`${mp4}: ${frames.length} frames, ${seconds} s`);
if (seconds > 120) throw new Error(`The video is ${seconds} s. The limit is 120 s.`);
