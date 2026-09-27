import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';

const $ = s => document.querySelector(s);
const meta = await (await fetch('data/meta.json')).json();
const raw = new Float32Array(await (await fetch('data/points.bin')).arrayBuffer());
const N = meta.items.length;
const P3 = raw.subarray(0, N * 3), P2 = raw.subarray(N * 3, N * 5);
$('#where').textContent = `${meta.root.split('/').pop()} · ${N.toLocaleString()} files · ${meta.model}`;
document.title = `view · ${meta.root.split('/').pop()}`;

// ---------- scene ----------
const canvas = $('#gl');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(devicePixelRatio);
renderer.setClearColor(0x0b0c0f);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(40, 1, 0.05, 500);
camera.position.set(14, 10, 30);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.zoomToCursor = true;

const p2as3 = new Float32Array(N * 3);
for (let i = 0; i < N; i++) { p2as3[i * 3] = P2[i * 2]; p2as3[i * 3 + 1] = P2[i * 2 + 1]; }
const colors = new Float32Array(N * 3), shown = new Float32Array(N).fill(1);
const geo = new THREE.BufferGeometry();
geo.setAttribute('position', new THREE.BufferAttribute(P3, 3));
geo.setAttribute('p2', new THREE.BufferAttribute(p2as3, 3));
geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
geo.setAttribute('shown', new THREE.BufferAttribute(shown, 1));
const baseSize = Math.max(2.5, Math.min(9, 40 / Math.sqrt(N / 100)));
const uniforms = { t: { value: 0 }, size: { value: baseSize * devicePixelRatio } };
const material = new THREE.ShaderMaterial({
  uniforms, transparent: true,
  vertexShader: `
    attribute vec3 p2; attribute vec3 color; attribute float shown;
    uniform float t, size; varying vec3 vc; varying float va;
    void main() {
      vec3 p = mix(position, p2, t);
      vc = color; va = shown;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
      gl_PointSize = size * mix(0.55, 1.0, shown);
    }`,
  fragmentShader: `
    varying vec3 vc; varying float va;
    void main() {
      vec2 q = gl_PointCoord - 0.5; float r = dot(q, q);
      if (r > 0.25) discard;
      gl_FragColor = vec4(vc, mix(0.12, 1.0, va) * smoothstep(0.25, 0.16, r));
    }`,
});
scene.add(new THREE.Points(geo, material));

// hover ring
const ring = new THREE.Mesh(new THREE.RingGeometry(0.8, 1, 32), new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false, transparent: true }));
ring.renderOrder = 9; ring.visible = false; scene.add(ring);

function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
}
addEventListener('resize', resize); resize();

// ---------- 2D <-> 3D ----------
// 3D layout is rotated onto its principal axes, so looking down -z is the 2D starting view.
let mode = 3, t = 0, tween = null, saved3d = null;
const ease = x => x * x * (3 - 2 * x);
function setMode(d) {
  if (d === mode) return;
  mode = d;
  document.querySelectorAll('#dims button').forEach(b => b.classList.toggle('on', +b.dataset.d === d));
  const from = { pos: camera.position.clone(), target: controls.target.clone(), up: camera.up.clone(), t };
  let to;
  if (d === 2) {
    saved3d = { pos: camera.position.clone(), target: controls.target.clone() };
    const dist = camera.position.distanceTo(controls.target);
    const target = controls.target.clone().setZ(0);
    to = { pos: target.clone().add(new THREE.Vector3(0, 0, dist)), target, up: new THREE.Vector3(0, 1, 0), t: 1 };
  } else {
    to = { ...(saved3d || { pos: new THREE.Vector3(14, 10, 30), target: new THREE.Vector3() }), up: new THREE.Vector3(0, 1, 0), t: 0 };
  }
  tween = { from, to, start: performance.now(), ms: 900 };
  controls.enabled = false;
}
function stepTween(now) {
  if (!tween) return;
  const k = ease(Math.min(1, (now - tween.start) / tween.ms)), { from, to } = tween;
  t = from.t + (to.t - from.t) * k; uniforms.t.value = t;
  // move along a sphere around the target so the camera swings, not slides
  const target = from.target.clone().lerp(to.target, k);
  const a = from.pos.clone().sub(from.target), b = to.pos.clone().sub(to.target);
  const len = a.length() + (b.length() - a.length()) * k;
  const dir = a.normalize().lerp(b.normalize(), k).normalize();
  if (dir.lengthSq() < 1e-6) dir.set(0, 0, 1);
  camera.position.copy(target).add(dir.multiplyScalar(len));
  controls.target.copy(target);
  camera.lookAt(target);
  if (k >= 1) {
    tween = null; controls.enabled = true;
    controls.enableRotate = mode === 3;
    controls.mouseButtons.LEFT = mode === 2 ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
    controls.screenSpacePanning = true;
    $('#hint').innerHTML = mode === 2
      ? 'drag to pan · scroll to zoom · <kbd>space</kbd> 2D/3D · click a point to pin'
      : 'drag to orbit · right-drag to pan · scroll to zoom · <kbd>space</kbd> 2D/3D · click a point to pin';
  }
}
document.querySelectorAll('#dims button').forEach(b => b.onclick = () => setMode(+b.dataset.d));
addEventListener('keydown', e => {
  if (e.target.tagName === 'SELECT') return;
  if (e.code === 'Space') { e.preventDefault(); setMode(mode === 3 ? 2 : 3); }
  if (e.code === 'Escape') unpin();
});

// ---------- color by ----------
const PALETTE = ['#4e9bff', '#ff7a45', '#35c68d', '#e35d9b', '#f4c542', '#9b7bff', '#3ec9d6', '#ff5a5a', '#9fd356', '#c78b5a',
  '#6f7dff', '#ff9ecf', '#20a39e', '#d0a8ff', '#ffb347', '#7ee0b0', '#b35dd4', '#5ab4e5', '#e8e36b', '#ff6f91'];
const NOISE = '#5d626e';
const cols = meta.columns;
const sel = $('#colorby');
for (const k of Object.keys(cols)) sel.add(new Option(k, k));
// default: the cluster level with the number of clusters closest to ~12
const nCats = k => new Set(cols[k]).size;
sel.value = Object.keys(cols).filter(k => k.startsWith('cluster') && nCats(k) > 1)
  .sort((a, b) => Math.abs(nCats(a) - 12) - Math.abs(nCats(b) - 12))[0] ?? 'type';
let hidden = new Set();
function applyColor() {
  const vals = cols[sel.value], legend = $('#legend'); legend.innerHTML = '';
  const numeric = vals.every(v => v === null || typeof v === 'number') && !sel.value.startsWith('cluster');
  const c = new THREE.Color();
  if (numeric) {
    const nums = vals.filter(v => v !== null), lo = Math.min(...nums), hi = Math.max(...nums);
    const ramp = x => c.setHSL(0.62 - 0.55 * x, 0.75, 0.35 + 0.3 * x);
    vals.forEach((v, i) => { v === null ? c.set(NOISE) : ramp((v - lo) / (hi - lo || 1)); c.toArray(colors, i * 3); shown[i] = 1; });
    legend.innerHTML = `<div class="ramp" style="background:linear-gradient(90deg,${[0, .5, 1].map(x => '#' + ramp(x).getHexString()).join(',')})"></div><div class="ends"><span>${lo}</span><span>${hi}</span></div>`;
  } else {
    const counts = new Map();
    vals.forEach(v => counts.set(v, (counts.get(v) || 0) + 1));
    const cats = [...counts.keys()].filter(v => v !== -1 && v !== null).sort((a, b) => counts.get(b) - counts.get(a));
    const colorOf = new Map(cats.map((v, i) => [v, PALETTE[i % PALETTE.length]]));
    vals.forEach((v, i) => { c.set(colorOf.get(v) || NOISE); c.toArray(colors, i * 3); shown[i] = hidden.has(v) ? 0 : 1; });
    const rows = [...cats, ...(counts.has(-1) ? [-1] : []), ...(counts.has(null) ? [null] : [])];
    for (const v of rows) {
      const r = document.createElement('div');
      r.className = 'row' + (hidden.has(v) ? ' off' : '');
      const label = v === -1 ? 'unclustered' : v === null ? 'no answer' : sel.value.startsWith('cluster') ? `cluster ${v}` : v;
      r.innerHTML = `<span class="sw" style="background:${colorOf.get(v) || NOISE}"></span><span></span><span class="n">${counts.get(v).toLocaleString()}</span>`;
      r.children[1].textContent = label;
      r.title = 'click to hide/show · alt-click to show only this';
      r.onclick = e => {
        if (e.altKey) hidden = new Set(rows.filter(x => x !== v));
        else hidden.has(v) ? hidden.delete(v) : hidden.add(v);
        applyColor();
      };
      legend.appendChild(r);
    }
  }
  geo.attributes.color.needsUpdate = true; geo.attributes.shown.needsUpdate = true;
}
sel.onchange = () => { hidden = new Set(); applyColor(); };
applyColor();

// ---------- hover + tooltip ----------
const tip = $('#tip');
let hover = -1, pinned = -1, mouse = null;
const m = new THREE.Matrix4(), v = new THREE.Vector3();
function pick(mx, my) {
  m.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  const e = m.elements, w = innerWidth, h = innerHeight, tt = uniforms.t.value;
  let best = -1, bestD = (Math.max(8, baseSize) + 4) ** 2, bestZ = Infinity;
  for (let i = 0; i < N; i++) {
    if (!shown[i]) continue;
    const x = P3[i * 3] + (p2as3[i * 3] - P3[i * 3]) * tt, y = P3[i * 3 + 1] + (p2as3[i * 3 + 1] - P3[i * 3 + 1]) * tt, z = P3[i * 3 + 2] * (1 - tt);
    const cw = e[3] * x + e[7] * y + e[11] * z + e[15];
    if (cw <= 0) continue;
    const sx = ((e[0] * x + e[4] * y + e[8] * z + e[12]) / cw * 0.5 + 0.5) * w;
    const sy = (0.5 - (e[1] * x + e[5] * y + e[9] * z + e[13]) / cw * 0.5) * h;
    const d = (sx - mx) ** 2 + (sy - my) ** 2;
    if (d < bestD - 4 || (d < bestD + 4 && cw < bestZ)) { best = i; bestD = Math.min(d, bestD); bestZ = cw; }
  }
  return best;
}
const fmtSize = b => b > 1e6 ? (b / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1e3)) + ' KB';
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
function showTip(i, mx, my) {
  const it = meta.items[i], parts = it.path.split('/'), name = parts.pop();
  const fields = Object.entries(cols).filter(([k]) => k !== 'type').map(([k, vs]) => {
    let val = vs[i];
    if (val === -1 && k.startsWith('cluster')) val = 'unclustered';
    return `<dt>${esc(k)}</dt><dd>${esc(val ?? '—')}</dd>`;
  }).join('');
  tip.innerHTML = `
    ${it.kind === 'image' ? `<img src="thumb/${i}" alt="">` : `<pre>${esc(it.snippet || '')}</pre>`}
    <div class="name">${esc(name)}</div>
    ${parts.length ? `<div class="dir">${esc(parts.join('/'))}</div>` : ''}
    <dl><dt>size</dt><dd>${fmtSize(it.size)}</dd><dt>modified</dt><dd>${new Date(it.mtime * 1000).toLocaleDateString()}</dd>${fields}</dl>
    <div class="links"><a data-act="reveal">Reveal in Finder</a><a data-act="open">Open</a></div>
    ${pinned === i ? '' : '<div class="pinhint">click the point to pin</div>'}`;
  tip.querySelectorAll('a').forEach(a => a.onclick = () => fetch(`api/${a.dataset.act}/${i}`, { method: 'POST', headers: { 'X-View': '1' } }));
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(mx + 16, innerWidth - r.width - 8) + 'px';
  tip.style.top = Math.max(8, Math.min(my + 16, innerHeight - r.height - 8)) + 'px';
}
function unpin() { pinned = -1; tip.classList.remove('pinned'); tip.hidden = true; }
canvas.addEventListener('pointermove', e => { mouse = [e.clientX, e.clientY]; });
canvas.addEventListener('pointerleave', () => { mouse = null; });
let down = null;
canvas.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
canvas.addEventListener('pointerup', e => {
  if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;  // was a drag
  const i = pick(e.clientX, e.clientY);
  if (i < 0) return unpin();
  pinned = i; tip.classList.add('pinned'); showTip(i, e.clientX, e.clientY);
});

function updateHover() {
  if (pinned >= 0) { hover = pinned; }
  else if (mouse && !tween) {
    const i = pick(mouse[0], mouse[1]);
    if (i !== hover) { hover = i; i >= 0 ? showTip(i, ...mouse) : (tip.hidden = true); }
    else if (i >= 0) showTip(i, ...mouse);
  } else if (!mouse) { hover = -1; tip.hidden = true; }
  ring.visible = hover >= 0;
  if (hover >= 0) {
    const tt = uniforms.t.value;
    ring.position.set(P3[hover * 3] + (p2as3[hover * 3] - P3[hover * 3]) * tt, P3[hover * 3 + 1] + (p2as3[hover * 3 + 1] - P3[hover * 3 + 1]) * tt, P3[hover * 3 + 2] * (1 - tt));
    ring.quaternion.copy(camera.quaternion);
    const s = ring.position.distanceTo(camera.position) * Math.tan(camera.fov * Math.PI / 360) * 2 / innerHeight * (baseSize + 5);
    ring.scale.setScalar(s);
  }
}

// ---------- loop ----------
let lastMove = 0;
canvas.addEventListener('pointermove', () => { lastMove = performance.now(); });
renderer.setAnimationLoop(now => {
  stepTween(now);
  if (!tween) controls.update();
  if (now - lastMove < 100 || pinned >= 0 || tween) updateHover();
  renderer.render(scene, camera);
});
window.__view = { setMode, get mode() { return mode; } };
