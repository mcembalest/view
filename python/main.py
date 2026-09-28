"""view's ML worker, embedded in the Go binary and run with uv by kit.Python. JSON lines over stdin/stdout.

  {"cmd": "build", "root": ..., "files": [[rel, kind, size, mtime], ...], "models": [...], "text_model": null | name}
      thumbnails + OCR for images, embeddings per model, 3D/2D layouts, EVoC clusters -> root/.view/
  {"cmd": "search", "q": ..., "model": ...}
      -> {"results": [[index, score], ...]}  max of: looks like (model), says (text model, if given)

Progress goes to stderr.
"""

import hashlib
import io
import json
import os
import re
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

warnings.filterwarnings("ignore")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

CACHE_VERSION = 2
ALIASES = {
    "mobileclip2-s0": "open_clip:MobileCLIP2-S0/dfndr2b",
    "mobileclip2": "open_clip:MobileCLIP2-S4/dfndr2b",
    "siglip2": "open_clip:ViT-SO400M-16-SigLIP2-384/webli",
    "clip": "sentence-transformers/clip-ViT-B-32",
    "qwen3-vl": "Qwen/Qwen3-VL-Embedding-2B",
    "minilm": "sentence-transformers/all-MiniLM-L6-v2",
    "qwen3-text": "Qwen/Qwen3-Embedding-0.6B",
}


def log(*a, end="\n"):
    print(*a, file=sys.stderr, flush=True, end=end)


def slug(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def key(f):
    return f"{f[0]}|{f[2]}|{int(f[3])}"


def thumb_name(f):
    return hashlib.sha1(key(f).encode()).hexdigest()[:16] + ".jpg"


def rgb(im):
    """RGB with transparency flattened onto white (a plain convert turns it black)."""
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        return Image.alpha_composite(Image.new("RGBA", im.size, "white"), im).convert("RGB")
    return im.convert("RGB")


def open_image(path, side=1024):
    im = Image.open(path)
    im.draft("RGB", (side, side))
    im = rgb(im)
    im.thumbnail((side, side))
    return im


def read_text(path, limit=200_000):
    return Path(path).read_bytes()[:limit].decode("utf-8", errors="ignore")


# ---------- per-image prep: thumbnail + OCR (Apple Vision, macOS) in separate processes ----------

def prep_image(args):
    path, thumb = args
    try:
        im = open_image(path, 1600)
    except Exception:
        return None
    if not os.path.exists(thumb):
        t = im.copy()
        t.thumbnail((256, 256))
        t.save(thumb, "JPEG", quality=82)
    try:
        import Vision
        from Foundation import NSData

        b = io.BytesIO()
        im.save(b, "JPEG", quality=90)
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(0)  # accurate
        Vision.VNImageRequestHandler.alloc().initWithData_options_(
            NSData.dataWithBytes_length_(b.getvalue(), len(b.getvalue())), None).performRequests_error_([req], None)
        return "\n".join(o.topCandidates_(1)[0].string() for o in (req.results() or []))
    except ImportError:
        return ""


def prep_images(root, files):
    """Thumbnails for every image; OCR text cached in .view/ocr.json. Returns {key: text}."""
    vdir = root / ".view"
    (vdir / "thumbs").mkdir(parents=True, exist_ok=True)
    cpath = vdir / "ocr.json"
    ocr = json.loads(cpath.read_text()) if cpath.exists() else {}
    todo = [f for f in files if f[1] == "image" and key(f) not in ocr]
    if todo:
        log(f"\n  reading images  {len(todo):,} (thumbnails + text in images)")
        t0 = time.time()
        with ProcessPoolExecutor(8) as pool:
            for n, (f, text) in enumerate(zip(todo, pool.map(prep_image, [(str(root / f[0]), str(vdir / "thumbs" / thumb_name(f))) for f in todo], chunksize=4)), 1):
                ocr[key(f)] = text if text is not None else ""
                if n % 20 == 0 or n == len(todo):
                    log(f"\r    {n:,}/{len(todo):,}  {n / (time.time() - t0):.0f} files/s   ", end="")
        log()
        cpath.write_text(json.dumps(ocr))
    return ocr


# ---------- models ----------

def device():
    import torch

    return "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"


class OpenCLIP:
    def __init__(self, spec):
        import open_clip
        import torch

        arch, pretrained = spec.split("/")
        self.torch, self.dev = torch, device()
        m, _, self.prep = open_clip.create_model_and_transforms(arch, pretrained=pretrained)
        self.tok = open_clip.get_tokenizer(arch)
        self.model = m.to(self.dev).eval().half()
        self.sees_images = True

    def images(self, ims):
        with self.torch.no_grad():
            return self.model.encode_image(self.torch.stack([self.prep(i) for i in ims]).to(self.dev).half()).float().cpu().numpy()

    def texts(self, docs, query=False):
        # the text tower sees ~77 tokens: embed 50-word chunks and average them
        pieces, owner = [], []
        for i, d in enumerate(docs):
            w = d.split()
            cs = [" ".join(w[j:j + 50]) for j in range(0, max(len(w), 1), 50)][:16]
            pieces += cs
            owner += [i] * len(cs)
        with self.torch.no_grad():
            E = np.concatenate([self.model.encode_text(self.tok(pieces[j:j + 256]).to(self.dev)).float().cpu().numpy()
                                for j in range(0, len(pieces), 256)])
        E /= np.linalg.norm(E, axis=1, keepdims=True)
        owner = np.array(owner)
        return np.stack([E[owner == i].mean(0) for i in range(len(docs))])


class ST:
    def __init__(self, name):
        import torch
        from sentence_transformers import SentenceTransformer

        self.m = SentenceTransformer(name, device=device(), trust_remote_code=True, model_kwargs={"torch_dtype": torch.float16})
        self.sees_images = "image" in self.m.modalities
        self.query_prompt = "query" in (self.m.prompts or {})

    def images(self, ims):
        return self.m.encode(ims, batch_size=16)

    def texts(self, docs, query=False):
        kw = {"prompt_name": "query"} if query and self.query_prompt else {}
        return self.m.encode([d[:8000] for d in docs], batch_size=16, **kw)


_loaded = {}


def load(name):
    if name not in _loaded:
        spec = ALIASES.get(name, name)
        log(f"  loading {name}")
        _loaded[name] = OpenCLIP(spec[len("open_clip:"):]) if spec.startswith("open_clip:") else ST(spec)
    return _loaded[name]


def describe(f, ocr):
    """How a file reads as text: its name, folder, and content (text files) or OCR (images)."""
    folder, _, name = f[0].rpartition("/")
    return f"{name}\n{folder}\n{ocr.get(key(f), '')}"


def embed(root, files, name, ocr, as_text=False):
    """(n, d) normalized vectors, cached per file. as_text: embed what each file says, not how it looks."""
    cdir = root / ".view" / "cache" / f"{slug(name)}{'-text' if as_text else ''}-v{CACHE_VERSION}"
    cdir.mkdir(parents=True, exist_ok=True)
    cache = {}
    if (cdir / "keys.json").exists():
        cache = dict(zip(json.loads((cdir / "keys.json").read_text()), np.load(cdir / "vecs.npy")))
    todo = [f for f in files if key(f) not in cache]
    log(f"\n  {name}{' (what files say)' if as_text else ''}  {len(todo):,} to embed · {len(files) - len(todo):,} cached")
    if todo:
        model, t0, done = load(name), time.time(), 0
        with ThreadPoolExecutor(8) as pool:
            for i in range(0, len(todo), 64):
                batch = todo[i:i + 64]
                pics = [f for f in batch if f[1] == "image" and model.sees_images and not as_text]
                ims = dict(zip(map(key, pics), pool.map(lambda f: _try_open(root / f[0]), pics)))
                pics = [f for f in pics if ims[key(f)] is not None]
                words = [f for f in batch if f not in pics]
                texts = list(pool.map(lambda f: (read_text(root / f[0]) + "\n" + f[0]) if f[1] == "text" else describe(f, ocr), words))
                if pics:
                    cache.update(zip(map(key, pics), np.asarray(model.images([ims[key(f)] for f in pics]), np.float32)))
                if words:
                    cache.update(zip(map(key, words), np.asarray(model.texts(texts), np.float32)))
                done += len(batch)
                rate = done / (time.time() - t0)
                log(f"\r    {done:,}/{len(todo):,}  {rate:.0f} files/s  ~{(len(todo) - done) / rate:.0f}s left   ", end="")
        log()
        ks = list(cache)
        np.save(cdir / "vecs.npy", np.stack([cache[k] for k in ks]))
        (cdir / "keys.json").write_text(json.dumps(ks))
    X = np.stack([cache[key(f)] for f in files]).astype(np.float32)
    return X / np.linalg.norm(X, axis=1, keepdims=True)


def _try_open(path):
    try:
        return open_image(path)
    except Exception:
        return None


# ---------- layout + clusters ----------

def _normalize(P):
    P = P - P.mean(0)
    return P / (np.abs(P).max() or 1) * 10


def project(X):
    """3D UMAP, then 2D UMAP started from the top-down view of 3D and rotated back onto it."""
    import umap

    n = len(X)
    if n < 5:
        P3 = np.random.default_rng(0).normal(size=(n, 3))
        return _normalize(P3), _normalize(P3[:, :2])
    kw = dict(metric="cosine", optimizer="adam", compatibility_layout=False, random_state=0, n_neighbors=min(15, n - 1))
    t = time.time()
    P3 = umap.UMAP(n_components=3, init="recursive", **kw).fit_transform(X)
    P3 -= P3.mean(0)
    P3 = _normalize(P3 @ np.linalg.svd(P3, full_matrices=False)[2].T)  # principal axes: looking down z = widest view
    log(f"    3D layout  {time.time() - t:.1f}s")
    t = time.time()
    P2 = umap.UMAP(n_components=2, init=P3[:, :2] / P3[:, :2].std(), **kw).fit_transform(X)
    A = P2 - P2.mean(0)
    U, _, Vt = np.linalg.svd(A.T @ P3[:, :2])
    P2 = A @ (U @ Vt)
    P2 *= P3[:, :2].std() / P2.std()
    log(f"    2D layout  {time.time() - t:.1f}s")
    return P3.astype(np.float32), P2.astype(np.float32)


def cluster(X):
    """EVoC clusters at several granularities (finest first, -1 = noise) and near-duplicate groups."""
    n = len(X)
    if n < 20:
        return [[0] * n], [None] * n
    import evoc

    c = evoc.EVoC(random_state=0)
    c.fit_predict(X)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in c.duplicates_:
        parent[root(a)] = root(b)
    paired = {i for p in c.duplicates_ for i in p}
    number = {}
    dupes = [number.setdefault(root(i), len(number) + 1) if i in paired else None for i in range(n)]
    return [[int(v) for v in layer] for layer in c.cluster_layers_], dupes


# ---------- search ----------

def zscore_by_kind(s, kinds):
    """text-text similarity always beats text-image (the modality gap): normalize within each file type."""
    s = s.copy()
    for k in set(kinds):
        m = kinds == k
        s[m] = (s[m] - s[m].mean()) / (s[m].std() + 1e-6)
    return s


class Index:
    """Search = max of two signals per file: what it looks like (the map's model) and, with a text model, what it
    says (text-file content, text read from images, and names). Images with no readable text only use the first."""

    def __init__(self, files, ocr):
        self.kinds = np.array([f[1] for f in files])
        self.says_something = np.array([f[1] == "text" or len(ocr.get(key(f), "")) > 15 for f in files])
        self.looks, self.says, self.text_model = {}, None, None

    def search(self, q, model, k=200):
        def sim(name, X):
            v = np.asarray(load(name).texts([q], query=True), np.float32)[0]
            return zscore_by_kind(X @ (v / np.linalg.norm(v)), self.kinds)

        score = sim(model, self.looks[model])
        if self.text_model:
            score = np.maximum(score, np.where(self.says_something, sim(self.text_model, self.says), -9))
        top = np.argsort(-score)[:k]
        return [[int(i), round(float(score[i]), 3)] for i in top]


def build(root, files, models, text_model=None):
    root = Path(root)
    out = root / ".view" / "map"
    out.mkdir(parents=True, exist_ok=True)
    ocr = prep_images(root, files)
    index = Index(files, ocr)
    sig = hashlib.sha1("\n".join(map(key, files)).encode()).hexdigest()
    for m in models:
        X = index.looks[m] = embed(root, files, m, ocr)
        s = slug(m)
        if (out / f"{s}.sig").exists() and (out / f"{s}.sig").read_text() == sig:
            continue
        P3, P2 = project(X)
        levels, dupes = cluster(X)
        log(f"    clusters   {' / '.join(str(len(set(l) - {-1})) for l in levels)}")
        np.concatenate([P3.ravel(), P2.ravel()]).astype(np.float32).tofile(out / f"{s}.bin")
        (out / f"{s}.json").write_text(json.dumps({"clusters": levels, "duplicates": dupes}))
        (out / f"{s}.sig").write_text(sig)
    if text_model:
        index.says, index.text_model = embed(root, files, text_model, ocr, as_text=True), text_model
    return index


def main():
    index = None
    for line in sys.stdin:
        req = json.loads(line)
        try:
            if req["cmd"] == "build":
                index = build(req["root"], req["files"], req["models"], req.get("text_model") or None)
                resp = {"ok": True}
            elif req["cmd"] == "search":
                resp = {"results": index.search(req["q"], req["model"])}
            else:
                resp = {"error": f"unknown cmd {req['cmd']}"}
        except Exception as e:
            import traceback

            traceback.print_exc()
            resp = {"error": str(e)}
        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
