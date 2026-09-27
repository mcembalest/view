"""Multimodal embeddings for text and image files, cached per file under .view/."""

import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .scan import File

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

THUMB = 256
TEXT_CHUNK_WORDS = 50  # MobileCLIP2's text tower sees 77 tokens; ~50 words fits
TEXT_MAX_CHUNKS = 16   # embedding = mean over the first chunks (benchmarked: more didn't help)


def key(f: File) -> str:
    return f"{f.rel}|{f.size}|{int(f.mtime)}"


def load_image(path: Path, thumb_path: Path) -> Image.Image | None:
    try:
        im = Image.open(path)
        im.draft("RGB", (1024, 1024))  # fast JPEG downscale while decoding
        im = ImageOps.exif_transpose(im.convert("RGB"))
        im.thumbnail((1024, 1024))
        if not thumb_path.exists():
            t = im.copy()
            t.thumbnail((THUMB, THUMB))
            t.save(thumb_path, "JPEG", quality=82)
        return im
    except Exception as e:
        print(f"    skip unreadable image {path.name}: {e}", file=sys.stderr)
        return None


def read_text(path: Path) -> str:
    return path.read_bytes()[:200_000].decode("utf-8", errors="ignore")


def chunks(text: str) -> list[str]:
    w = text.split()
    return [" ".join(w[i:i + TEXT_CHUNK_WORDS]) for i in range(0, max(len(w), 1), TEXT_CHUNK_WORDS)][:TEXT_MAX_CHUNKS]


class MobileCLIP2:
    """Apple MobileCLIP2-S4 via open_clip. Fast on Apple Silicon; strong on images, weaker on long text."""

    name = "mobileclip2"

    def __init__(self, device):
        import open_clip
        import torch

        self.torch, self.device = torch, device
        m, _, self.prep = open_clip.create_model_and_transforms("MobileCLIP2-S4", pretrained="dfndr2b")
        self.tok = open_clip.get_tokenizer("MobileCLIP2-S4")
        self.model = m.to(device).eval().half()

    def images(self, ims):
        with self.torch.no_grad():
            x = self.torch.stack([self.prep(im) for im in ims]).to(self.device).half()
            return self.model.encode_image(x).float().cpu().numpy()

    def texts(self, docs):
        pieces, owner = [], []
        for i, d in enumerate(docs):
            cs = chunks(d)
            pieces += cs
            owner += [i] * len(cs)
        with self.torch.no_grad():
            E = np.concatenate([self.model.encode_text(self.tok(pieces[i:i + 256]).to(self.device)).float().cpu().numpy()
                                for i in range(0, len(pieces), 256)])
        E /= np.linalg.norm(E, axis=1, keepdims=True)
        owner = np.array(owner)
        return np.stack([E[owner == i].mean(0) for i in range(len(docs))])


class Qwen3VL:
    """Qwen3-VL-Embedding-2B. Better on long text and reads text inside images, but ~40x slower here."""

    name = "qwen3-vl"

    def __init__(self, device):
        import torch
        from sentence_transformers import SentenceTransformer

        self.m = SentenceTransformer("Qwen/Qwen3-VL-Embedding-2B", device=device, model_kwargs={"torch_dtype": torch.float16})

    def images(self, ims):
        return self.m.encode(ims, batch_size=8)

    def texts(self, docs):
        return self.m.encode([d[:8000] for d in docs], batch_size=8)


MODELS = {"mobileclip2": MobileCLIP2, "qwen3-vl": Qwen3VL}


def embed(root: Path, files: list[File], model_name: str) -> np.ndarray:
    """Return an (n, d) L2-normalized matrix, reusing cached vectors for unchanged files."""
    vdir = root / ".view"
    cdir = vdir / "cache" / model_name
    tdir = vdir / "thumbs"
    cdir.mkdir(parents=True, exist_ok=True)
    tdir.mkdir(exist_ok=True)
    cache: dict[str, np.ndarray] = {}
    if (cdir / "keys.json").exists():
        ks = json.loads((cdir / "keys.json").read_text())
        cache = dict(zip(ks, np.load(cdir / "vecs.npy")))
    todo = [f for f in files if key(f) not in cache]
    print(f"\n  embedding  {len(todo):,} new · {len(files) - len(todo):,} cached   model {model_name}")
    if todo:
        import torch

        device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
        model = MODELS[model_name](device)
        t0, done = time.time(), 0
        for kind, batch_size in (("text", 64), ("image", 64)):
            group = [f for f in todo if f.kind == kind]
            with ThreadPoolExecutor(8) as pool:
                for i in range(0, len(group), batch_size):
                    batch = group[i:i + batch_size]
                    if kind == "image":
                        loaded = list(pool.map(lambda f: load_image(root / f.rel, tdir / thumb_name(f)), batch))
                        ok = [(f, im) for f, im in zip(batch, loaded) if im is not None]
                        vecs = model.images([im for _, im in ok]) if ok else []
                    else:
                        ok = [(f, t) for f, t in zip(batch, pool.map(lambda f: read_text(root / f.rel), batch))]
                        vecs = model.texts([t for _, t in ok])
                    for (f, _), v in zip(ok, vecs):
                        cache[key(f)] = np.asarray(v, dtype=np.float32)
                    done += len(batch)
                    rate = done / (time.time() - t0)
                    print(f"\r    {done:,}/{len(todo):,}  {rate:.0f} files/s  ~{(len(todo) - done) / rate:.0f}s left   ", end="", flush=True)
        print()
        ks = list(cache)
        np.save(cdir / "vecs.npy", np.stack([cache[k] for k in ks]))
        (cdir / "keys.json").write_text(json.dumps(ks))
    keep = [f for f in files if key(f) in cache]
    files[:] = keep  # drop unreadable files
    X = np.stack([cache[key(f)] for f in keep]).astype(np.float32)
    return X / np.linalg.norm(X, axis=1, keepdims=True)


def thumb_name(f: File) -> str:
    return hashlib.sha1(key(f).encode()).hexdigest()[:16] + ".jpg"
