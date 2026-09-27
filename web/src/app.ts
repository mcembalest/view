import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

type Value = string | number | null;
type Item = { path: string; kind: string; size: number; mtime: number };
type Meta = { root: string; models: { name: string; slug: string }[]; items: [string, string, number, number][]; columns: Record<string, Value[]> };

const $ = <T extends HTMLElement = HTMLElement>(s: string) => document.querySelector(s) as T;
const esc = (s: unknown) => String(s ?? '—').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const post = (url: string, body?: object) => fetch(url, { method: 'POST', headers: { 'X-View': '1' }, body: JSON.stringify(body ?? {}) }).then(r => r.json());

// ---------- data: files, a 3D + 2D layout per model, and every field you can color/group by ----------
const meta: Meta = await (await fetch('data/meta.json')).json();
const items: Item[] = meta.items.map(([path, kind, size, mtime]) => ({ path, kind, size, mtime }));
const N = items.length, folder = meta.root.split('/').pop()!;
$('#where').textContent = `${folder} · ${N.toLocaleString()} files`;
document.title = `view · ${folder}`;

const layouts: Record<string, { 3: Float32Array; 2: Float32Array }> = {};
const fields: Record<string, Value[]> = {};
const clusterChoices: [string, number][] = [];
for (const m of meta.models) {
  const raw = new Float32Array(await (await fetch(`data/${m.slug}.bin`)).arrayBuffer()), flat = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) flat.set([raw[N * 3 + i * 2], raw[N * 3 + i * 2 + 1], 0], i * 3);
  layouts[m.name] = { 3: raw.slice(0, N * 3), 2: flat };
  const { clusters, duplicates }: { clusters: number[][]; duplicates: (number | null)[] } = await (await fetch(`data/${m.slug}.json`)).json();
  const tag = meta.models.length > 1 ? ` · ${m.name}` : '';
  for (const level of clusters) {
    const n = new Set(level.filter(v => v >= 0)).size, k = `clusters · ${n}${tag}`;
    fields[k] = level;
    if (m === meta.models[0]) clusterChoices.push([k, n]);
  }
  if (duplicates.some(d => d !== null)) fields[`near-duplicates${tag}`] = duplicates;
}
const dir = (p: string) => p.includes('/') ? p.slice(0, p.lastIndexOf('/') + 1) : '(top level)';
Object.assign(fields, {
  'type': items.map(it => it.kind),
  'extension': items.map(it => it.path.split('.').pop()!.toLowerCase()),
  'folder': items.map(it => it.path.includes('/') ? it.path.split('/')[0] + '/' : '(top level)'),
  'parent folder': items.map(it => dir(it.path)),
  'modified month': items.map(it => new Date(it.mtime * 1000).toISOString().slice(0, 7)),
  'size (KB)': items.map(it => Math.round(it.size / 1024)),
}, meta.columns);
const isGroupField = (k: string) => k.startsWith('clusters') || k.startsWith('near-dup');
const label = (k: string, v: Value | undefined) => v === -1 ? 'unclustered' : v === null || v === undefined ? '—'
  : k.startsWith('clusters') ? `cluster ${v}` : k.startsWith('near-dup') ? `duplicate group ${v}` : v;

// ---------- scene ----------
const canvas = $<HTMLCanvasElement>('#gl'), renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(devicePixelRatio);
renderer.setClearColor(0x0b0c0f);
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(40, 1, 0.05, 500);
camera.position.set(14, 10, 30);
const controls = new OrbitControls(camera, canvas);
Object.assign(controls, { enableDamping: true, zoomToCursor: true, screenSpacePanning: true });
const resize = () => { renderer.setSize(innerWidth, innerHeight, false); camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); };
addEventListener('resize', resize); resize();

const from = new Float32Array(N * 3), to = new Float32Array(N * 3), colors = new Float32Array(N * 3), lit = new Float32Array(N).fill(1);
const geo = new THREE.BufferGeometry();
for (const [k, a, n] of [['position', from, 3], ['to', to, 3], ['color', colors, 3], ['lit', lit, 1]] as const) geo.setAttribute(k, new THREE.BufferAttribute(a, n));
const dirty = (...ks: string[]) => ks.forEach(k => { geo.attributes[k].needsUpdate = true; });
const size = Math.max(2.5, Math.min(9, 40 / Math.sqrt(N / 100)));
const uniforms = { t: { value: 1 }, size: { value: size * devicePixelRatio } };
scene.add(new THREE.Points(geo, new THREE.ShaderMaterial({
  uniforms, transparent: true, depthWrite: false,
  vertexShader: `attribute vec3 to, color; attribute float lit; uniform float t, size; varying vec3 vc; varying float va;
    void main() { vc = color; va = lit; gl_Position = projectionMatrix * modelViewMatrix * vec4(mix(position, to, t), 1.0);
      gl_PointSize = size * mix(0.6, 1.0, lit); }`,
  fragmentShader: `varying vec3 vc; varying float va;
    void main() { vec2 q = gl_PointCoord - 0.5; float r = dot(q, q); if (r > 0.25) discard;
      gl_FragColor = vec4(mix(vec3(0.3), vc, 0.2 + 0.8 * va), mix(0.15, 1.0, va) * smoothstep(0.25, 0.16, r)); }`,
})));
const ring = new THREE.Mesh(new THREE.RingGeometry(0.8, 1, 32), new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false }));
ring.renderOrder = 9; scene.add(ring);
const pos = (i: number): [number, number, number] => {
  const t = uniforms.t.value, p = (k: number) => from[i * 3 + k] + (to[i * 3 + k] - from[i * 3 + k]) * t;
  return [p(0), p(1), p(2)];
};

// ---------- state (model, dims, color, text live in the URL so a reload comes back to the same view) ----------
const h = new URLSearchParams(location.hash.slice(1));
const S = {
  model: layouts[h.get('model') ?? ''] ? h.get('model')! : meta.models[0].name,
  dims: (h.get('dims') === '2' ? 2 : 3) as 2 | 3,
  color: fields[h.get('color') ?? ''] ? h.get('color')! : clusterChoices.sort((a, b) => Math.abs(a[1] - 12) - Math.abs(b[1] - 12))[0]?.[0] ?? 'type',
  text: h.get('q') ?? '',
  meaning: null as [number, number][] | null,
  groups: null as Set<Value> | null,
  box: null as Set<number> | null,
};
const saveHash = () => history.replaceState(null, '', '#' + new URLSearchParams({ model: S.model, dims: String(S.dims), color: S.color, ...(S.text && { q: S.text }) }));

// ---------- motion: any change of model or 2D/3D animates points from where they are now ----------
let anim: { t0: THREE.Vector3; o0: THREE.Vector3; t1: THREE.Vector3; o1: THREE.Vector3; start: number; ms: number } | null = null;
function go(model: string, dims: 2 | 3) {
  for (let i = 0; i < N; i++) from.set(pos(i), i * 3);
  to.set(layouts[model][dims]);
  dirty('position', 'to');
  uniforms.t.value = 0;
  const target = controls.target.clone(), dist = camera.position.distanceTo(target);
  if (dims === 2) target.z = 0;
  const view = dims === 2 ? new THREE.Vector3(0, 0, 1) : dims !== S.dims ? new THREE.Vector3(.4, .3, .87) : camera.position.clone().sub(target).normalize();
  fly(target, view.multiplyScalar(dist), 900);
  Object.assign(S, { model, dims });
  document.querySelectorAll<HTMLButtonElement>('#dims button').forEach(b => b.classList.toggle('on', +b.dataset.v! === dims));
  controls.enableRotate = dims === 3;
  controls.mouseButtons.LEFT = dims === 3 ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
  $('#hint').innerHTML = `${dims === 3 ? 'drag to orbit · right-drag to pan' : 'drag to pan'} · scroll to zoom · <kbd>shift</kbd>-drag to select · <kbd>space</kbd> 2D/3D · <kbd>?</kbd> help`;
  $<HTMLSelectElement>('#model').value = model;
  saveHash();
}
function fly(target: THREE.Vector3, offset: THREE.Vector3, ms: number) {
  anim = { t0: controls.target.clone(), o0: camera.position.clone().sub(controls.target), t1: target, o1: offset, start: performance.now(), ms };
  controls.enabled = false;
}
function stepAnim(now: number) {
  if (!anim) return;
  const x = Math.min(1, (now - anim.start) / anim.ms), k = x * x * (3 - 2 * x);
  if (uniforms.t.value < 1) uniforms.t.value = k;
  const target = anim.t0.clone().lerp(anim.t1, k), len = anim.o0.length() + (anim.o1.length() - anim.o0.length()) * k;
  camera.position.copy(target).add(anim.o0.clone().normalize().lerp(anim.o1.clone().normalize(), k).normalize().multiplyScalar(len));
  controls.target.copy(target); camera.lookAt(target);
  if (x >= 1) { anim = null; uniforms.t.value = 1; controls.enabled = true; }
}

// ---------- color + legend (clicking a group narrows the set to it) ----------
const PALETTE = ['#4e9bff', '#ff7a45', '#35c68d', '#e35d9b', '#f4c542', '#9b7bff', '#3ec9d6', '#ff5a5a', '#9fd356', '#c78b5a',
  '#6f7dff', '#ff9ecf', '#20a39e', '#d0a8ff', '#ffb347', '#7ee0b0', '#b35dd4', '#5ab4e5', '#e8e36b', '#ff6f91'];
const GRAY = '#5d626e';
function paint() {
  const k = S.color, vals = fields[k], c = new THREE.Color(), legend = $('#legend');
  legend.innerHTML = '';
  if (!isGroupField(k) && vals.every(v => v === null || typeof v === 'number')) {
    const nums = (vals.filter(v => v !== null) as number[]).sort((a, b) => a - b), q = (x: number) => nums[Math.floor(x * (nums.length - 1))];
    const lo = q(.02), hi = q(.98), ramp = (x: number) => c.setHSL(.62 - .55 * x, .75, .35 + .3 * x);
    vals.forEach((v, i) => (v === null ? c.set(GRAY) : ramp(Math.max(0, Math.min(1, ((v as number) - lo) / (hi - lo || 1))))).toArray(colors, i * 3));
    legend.innerHTML = `<div class="ramp" style="background:linear-gradient(90deg,${[0, .5, 1].map(x => '#' + ramp(x).getHexString())})"></div><div class="ends"><span>${lo}</span><span>${hi}</span></div>`;
  } else {
    const counts = new Map<Value, number>(), junk = (v: Value) => v === -1 || v === null || v === undefined;
    vals.forEach(v => counts.set(v, (counts.get(v) ?? 0) + 1));
    const groups = [...counts.keys()].sort((a, b) => +junk(a) - +junk(b) || counts.get(b)! - counts.get(a)!);
    const colorOf = new Map(groups.filter(g => !junk(g)).slice(0, PALETTE.length).map((g, i) => [g, PALETTE[i]]));
    vals.forEach((v, i) => c.set(colorOf.get(v) ?? GRAY).toArray(colors, i * 3));
    for (const g of groups.slice(0, 300)) {
      const row = legend.appendChild(document.createElement('div'));
      row.className = 'g' + (S.groups && !S.groups.has(g) ? ' off' : '');
      row.title = 'click: only this · shift-click: add/remove';
      row.innerHTML = `<span class="sw" style="background:${colorOf.get(g) ?? GRAY}"></span><span class="l">${esc(label(k, g))}</span><span class="n">${counts.get(g)!.toLocaleString()}</span>`;
      row.onclick = e => {
        if (e.shiftKey || e.metaKey) { S.groups ??= new Set(); S.groups.has(g) ? S.groups.delete(g) : S.groups.add(g); if (!S.groups.size) S.groups = null; }
        else S.groups = S.groups?.size === 1 && S.groups.has(g) ? null : new Set([g]);
        paint();
      };
    }
  }
  dirty('color');
  update();
}

// ---------- the set = text (names, or meaning) ∧ legend groups ∧ drag selection; lit on the map, shown in the grid ----------
let set: number[] = [];
function update() {
  const vals = fields[S.color], terms = S.text.toLowerCase().split(/\s+/).filter(Boolean);
  const yes = terms.filter(t => t[0] !== '-'), no = terms.filter(t => t[0] === '-' && t.length > 1).map(t => t.slice(1));
  const rank = S.meaning && new Map(S.meaning.map(([i], r) => [i, r]));
  set = [];
  for (let i = 0; i < N; i++) {
    const p = items[i].path.toLowerCase();
    lit[i] = +((!S.groups || S.groups.has(vals[i])) && (!S.box || S.box.has(i)) && (rank ? rank.has(i) : yes.every(t => p.includes(t)) && !no.some(t => p.includes(t))));
    if (lit[i]) set.push(i);
  }
  if (rank) set.sort((a, b) => rank.get(a)! - rank.get(b)!);
  dirty('lit');
  const all = set.length === N;
  $('#status').innerHTML = all ? `${N.toLocaleString()} files` :
    `<span>${set.length.toLocaleString()} of ${N.toLocaleString()}${S.meaning ? ' · closest in meaning' : ''}</span><a id="clear">clear</a><a id="copy">copy paths</a>`;
  if (!all) {
    $('#clear').onclick = clear;
    $('#copy').onclick = () => navigator.clipboard.writeText(set.map(i => `${meta.root}/${items[i].path}`).join('\n'));
  }
  $('#grid').innerHTML = set.slice(0, 300).map(i => `<div data-i="${i}" title="${esc(items[i].path)}"${items[i].kind === 'image'
    ? `><img src="thumb/${i}" loading="lazy">` : ` class="t">${esc(items[i].path.split('/').pop())}`}</div>`).join('') +
    (set.length > 300 ? `<div class="more">+ ${(set.length - 300).toLocaleString()} more</div>` : '');
  saveHash();
}
function clear() { Object.assign(S, { text: '', meaning: null, groups: null, box: null }); q.value = ''; paint(); }

const q = $<HTMLInputElement>('#q');
q.value = S.text;
q.oninput = () => { S.text = q.value; S.meaning = null; update(); };
q.onkeydown = async e => {
  if (e.key === 'Enter' && q.value.trim()) {
    $('#status').textContent = 'searching by meaning…';
    S.meaning = (await post('api/search', { q: q.value, model: S.model })).slice(0, 100);
    update();
  }
  if (e.key === 'Escape') { q.value = ''; S.text = ''; S.meaning = null; update(); q.blur(); }
};

// ---------- pointing: hover to peek, click to pin (point or grid tile), shift-drag to select ----------
const m4 = new THREE.Matrix4();
function screen(i: number): [number, number, number] | null {
  const e = m4.elements, [x, y, z] = pos(i), w = e[3] * x + e[7] * y + e[11] * z + e[15];
  return w <= 0 ? null : [((e[0] * x + e[4] * y + e[8] * z + e[12]) / w * .5 + .5) * innerWidth, (.5 - (e[1] * x + e[5] * y + e[9] * z + e[13]) / w * .5) * innerHeight, w];
}
function pick(mx: number, my: number) {
  m4.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  let best = -1, bestD = (size + 6) ** 2, bestW = Infinity;
  for (let i = 0; i < N; i++) {
    const p = lit[i] ? screen(i) : null;
    if (!p) continue;
    const d = (p[0] - mx) ** 2 + (p[1] - my) ** 2;
    if (d < bestD - 4 || (d < bestD + 4 && p[2] < bestW)) { best = i; bestD = Math.min(d, bestD); bestW = p[2]; }
  }
  return best;
}
const tip = $('#tip');
let hover = -1, pinned = -1, tileHover = -1, mouse: [number, number] | null = null, drag: { start: [number, number]; select: boolean } | null = null, lastMove = 0;
async function showTip(i: number, x: number, y: number) {
  const it = items[i], parts = it.path.split('/'), file = parts.pop();
  const rows = [...new Set([S.color, ...Object.keys(meta.columns)])].map(k => `<dt>${esc(k)}</dt><dd>${esc(label(k, fields[k][i]))}</dd>`).join('');
  tip.innerHTML = `${it.kind === 'image' ? `<img src="thumb/${i}">` : '<pre></pre>'}<b>${esc(file)}</b>${parts.length ? `<div class="dim">${esc(parts.join('/'))}</div>` : ''}
    <dl><dt>size</dt><dd>${Math.round(it.size / 1024)} KB</dd><dt>modified</dt><dd>${new Date(it.mtime * 1000).toLocaleDateString()}</dd>${rows}</dl>
    ${pinned === i ? '<p><a data-a="reveal">Reveal in Finder</a><a data-a="open">Open</a></p>' : ''}`;
  tip.querySelectorAll<HTMLAnchorElement>('a').forEach(a => a.onclick = () => post(`api/${a.dataset.a}/${i}`));
  tip.classList.toggle('pinned', pinned === i);
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.max(8, Math.min(x + 16, innerWidth - r.width - 344)) + 'px';
  tip.style.top = Math.max(8, Math.min(y + 16, innerHeight - r.height - 8)) + 'px';
  if (it.kind === 'text') {
    const txt = await (await fetch(`peek/${i}`)).text(), pre = tip.querySelector('pre');
    if (pre && (hover === i || pinned === i)) pre.textContent = txt;
  }
}
function pin(i: number, flyTo = false) {
  pinned = i;
  if (i < 0) { tip.hidden = true; return; }
  const place = () => { m4.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse); const p = screen(i); if (p) showTip(i, p[0], p[1]); };
  if (!flyTo) return place();
  fly(new THREE.Vector3(...pos(i)), camera.position.clone().sub(controls.target), 600);  // pan to it, keep the zoom
  setTimeout(place, 650);
}
const grid = $('#grid'), tileOf = (e: Event) => (e.target as HTMLElement).closest<HTMLElement>('[data-i]')?.dataset.i;
grid.onmouseover = e => { tileHover = +(tileOf(e) ?? -1); };
grid.onmouseleave = () => { tileHover = -1; };
grid.onclick = e => { const i = tileOf(e); if (i !== undefined) pin(+i, true); };
canvas.addEventListener('pointermove', e => {
  mouse = [e.clientX, e.clientY]; lastMove = performance.now();
  if (!drag?.select) return;
  const [x0, y0] = drag.start;
  Object.assign($('#box').style, { left: Math.min(x0, e.clientX) + 'px', top: Math.min(y0, e.clientY) + 'px', width: Math.abs(e.clientX - x0) + 'px', height: Math.abs(e.clientY - y0) + 'px' });
  $('#box').hidden = false;
});
canvas.addEventListener('pointerleave', () => { mouse = null; });
canvas.addEventListener('pointerdown', e => { drag = { start: [e.clientX, e.clientY], select: e.shiftKey }; if (e.shiftKey) controls.enabled = false; });
addEventListener('pointerup', e => {
  if (!drag) return;
  const [x0, y0] = drag.start, moved = Math.hypot(e.clientX - x0, e.clientY - y0) > 4;
  if (drag.select && moved) {
    m4.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    const [xa, xb, ya, yb] = [Math.min(x0, e.clientX), Math.max(x0, e.clientX), Math.min(y0, e.clientY), Math.max(y0, e.clientY)];
    S.box = new Set();
    for (let i = 0; i < N; i++) { const p = lit[i] ? screen(i) : null; if (p && p[0] >= xa && p[0] <= xb && p[1] >= ya && p[1] <= yb) S.box.add(i); }
    $('#box').hidden = true; controls.enabled = true; update();
  } else if (!moved && e.target === canvas) pin(pick(e.clientX, e.clientY));
  drag = null;
});

// ---------- controls ----------
const help = $('#help'), helpBtn = $('#help-btn');
const toggleHelp = (show: boolean = help.hidden === true) => { help.hidden = !show; helpBtn.classList.toggle('on', show); };
helpBtn.onclick = () => toggleHelp();
addEventListener('keydown', e => {
  const tag = (e.target as HTMLElement).tagName;
  if (tag === 'INPUT' || tag === 'SELECT') return;
  if (e.key === '?') { toggleHelp(); return; }
  if (e.code === 'Space') { e.preventDefault(); go(S.model, S.dims === 3 ? 2 : 3); }
  if (e.key === '/') { e.preventDefault(); q.focus(); }
  if (e.key === 'Escape') !help.hidden ? toggleHelp(false) : pinned >= 0 ? pin(-1) : clear();
});
document.querySelectorAll<HTMLButtonElement>('#dims button').forEach(b => b.onclick = () => go(S.model, +b.dataset.v! as 2 | 3));
const msel = $<HTMLSelectElement>('#model'), csel = $<HTMLSelectElement>('#color');
meta.models.forEach(m => msel.add(new Option(m.name, m.name)));
msel.hidden = meta.models.length < 2;
msel.onchange = () => go(msel.value, S.dims);
Object.keys(fields).forEach(k => csel.add(new Option(k, k)));
csel.value = S.color;
csel.onchange = () => { S.color = csel.value; S.groups = null; paint(); };

renderer.setAnimationLoop(now => {
  stepAnim(now);
  if (!anim) controls.update();
  if (pinned < 0 && mouse && !drag && now - lastMove < 80) {
    const i = pick(...mouse);
    if (i !== hover) { hover = i; if (i >= 0) showTip(i, ...mouse); else tip.hidden = true; }
  }
  const at = tileHover >= 0 ? tileHover : pinned >= 0 ? pinned : tip.hidden ? -1 : hover;
  ring.visible = at >= 0;
  if (ring.visible) {
    ring.position.set(...pos(at)); ring.quaternion.copy(camera.quaternion);
    ring.scale.setScalar(ring.position.distanceTo(camera.position) * Math.tan(camera.fov * Math.PI / 360) * 2 / innerHeight * (size + 6));
  }
  renderer.render(scene, camera);
});
const initialDims = S.dims; S.dims = 3;
go(S.model, initialDims);
paint();
