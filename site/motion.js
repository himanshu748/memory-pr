// Presentation only: the drifting graph behind the first screen and the proof card's replay.
// Nothing here is needed to read or use the page, and all of it is off with reduced motion.
const calm = matchMedia('(prefers-reduced-motion: reduce)');
const $ = (id) => document.getElementById(id);

/** A quiet node-link field in ink. Nodes drift, link to near neighbours and lean away from the pointer. */
function field(canvas) {
  const ctx = canvas.getContext('2d');
  const ink = getComputedStyle(document.documentElement).getPropertyValue('--ink').trim() || '#141412';
  const pointer = { x: -1e4, y: -1e4 };
  let nodes = [], w = 0, h = 0, raf = 0, seen = true, seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646; // same field on every visit
  function size() {
    const box = canvas.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2);
    [w, h] = [box.width, box.height];
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const want = Math.round(Math.min(64, Math.max(18, (w * h) / 22000)));
    while (nodes.length < want) nodes.push({ x: rnd() * w, y: rnd() * h, vx: (rnd() - 0.5) * 0.22, vy: (rnd() - 0.5) * 0.22, r: 1.6 + rnd() * 2.6 });
    nodes.length = want;
    frame(false);
  }
  function frame(move) {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = ctx.strokeStyle = ink;
    for (const n of nodes) {
      if (move) {
        const dx = n.x - pointer.x, dy = n.y - pointer.y, d = Math.hypot(dx, dy);
        if (d < 170 && d > 1) { n.x += (dx / d) * (1 - d / 170) * 1.4; n.y += (dy / d) * (1 - d / 170) * 1.4; }
        n.x += n.vx; n.y += n.vy;
        if (n.x < -20) n.x = w + 20; else if (n.x > w + 20) n.x = -20;
        if (n.y < -20) n.y = h + 20; else if (n.y > h + 20) n.y = -20;
      }
    }
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j], d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d > 190) continue;
        ctx.globalAlpha = 0.2 * (1 - d / 190);
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      }
      ctx.globalAlpha = 0.24;
      ctx.beginPath(); ctx.arc(a.x, a.y, a.r, 0, 6.3); ctx.fill();
    }
  }
  const tick = () => { frame(true); raf = requestAnimationFrame(tick); };
  const sync = () => {
    cancelAnimationFrame(raf);
    raf = 0;
    if (seen && !document.hidden && !calm.matches) raf = requestAnimationFrame(tick);
  };
  new ResizeObserver(size).observe(canvas);
  new IntersectionObserver(([e]) => { seen = e.isIntersecting; sync(); }).observe(canvas);
  document.addEventListener('visibilitychange', sync);
  calm.addEventListener('change', sync);
  addEventListener('pointermove', (e) => {
    const box = canvas.getBoundingClientRect();
    pointer.x = e.clientX - box.left; pointer.y = e.clientY - box.top;
  }, { passive: true });
}

/** Play the proof card's story again: on hover, and every so often while it is on screen. */
function proof(card) {
  let seen = false, hover = false;
  const replay = () => {
    if (calm.matches) return;
    card.classList.add('still');
    void card.offsetWidth; // restart the CSS animations
    card.classList.remove('still');
  };
  card.addEventListener('pointerenter', (e) => { hover = true; if (e.pointerType === 'mouse') replay(); });
  card.addEventListener('pointerleave', () => (hover = false));
  new IntersectionObserver(([e]) => (seen = e.isIntersecting), { threshold: 0.6 }).observe(card);
  setInterval(() => seen && !hover && !document.hidden && replay(), 12000);
}

/** Sections rise in as they enter the screen. The class that hides them is set here, so without this script they are simply visible. */
function reveals() {
  if (calm.matches || !('IntersectionObserver' in window)) return;
  const seen = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.classList.add('in');
      seen.unobserve(e.target);
      e.target.querySelectorAll('[data-count]').forEach(count);
    }
  }, { rootMargin: '0px 0px -12% 0px' });
  document.documentElement.classList.add('reveal-on');
  document.querySelectorAll('[data-reveal]').forEach((el) => seen.observe(el));
  const flow = document.querySelector('.flow');
  if (flow) flow.appendChild(document.createElement('i')).className = 'packet';
}

/** Count from the figure without the check to the figure with it. The page holds the final number. */
function count(el) {
  const [from, to] = [Number(el.dataset.from), Number(el.dataset.count)];
  const start = performance.now() + 250;
  const step = (now) => {
    const k = Math.min(1, Math.max(0, (now - start) / 1300));
    el.textContent = Math.round(from + (to - from) * (1 - (1 - k) ** 3));
    if (k < 1) requestAnimationFrame(step);
  };
  el.textContent = from;
  requestAnimationFrame(step);
}

/** The primary buttons lean a few pixels toward the pointer. */
function magnetic(el) {
  el.addEventListener('pointermove', (e) => {
    if (calm.matches || e.pointerType !== 'mouse') return;
    const box = el.getBoundingClientRect();
    el.style.transform = `translate(${((e.clientX - box.left) / box.width - 0.5) * 10}px, ${((e.clientY - box.top) / box.height - 0.5) * 8}px)`;
  });
  el.addEventListener('pointerleave', () => (el.style.transform = ''));
}

reveals();
document.querySelectorAll('.btn').forEach(magnetic);
if ($('field')) field($('field'));
document.querySelectorAll('.proof').forEach(proof);
