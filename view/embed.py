"""Embed files with any model: open_clip, or anything sentence-transformers can load.

Models that can't see images (text-only) embed images from a text description (name, folder, size).
Vectors are cached per model per file under .view/cache/.
"""

import hashlib
import json
import re
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

CACHE_VERSION = 2  # bump when preprocessing changes

ALIASES = {
    "mobileclip2": "open_clip:MobileCLIP2-S4/dfndr2b",
    "siglip2": "open_clip:ViT-SO400M-16-SigLIP2-384/webli",
    "clip": "sentence-transformers/clip-ViT-B-32",
    "qwen3-vl": "Qwen/Qwen3-VL-Embedding-2B",
    "minilm": "sentence-transformers/all-MiniLM-L6-v2",
    "qwen3-text": "Qwen/Qwen3-Embedding-0.6B",
}


def resolve(name: str) -> str:
    return ALIASES.get(name, name)


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def key(f: File) -> str:
    return f"{f.rel}|{f.size}|{int(f.mtime)}"


def thumb_name(f: File) -> str:
    return hashlib.sha1(key(f).encode()).hexdigest()[:16] + ".jpg"


def read_text(path: Path, limit=200_000) -> str:
    return path.read_bytes()[:limit].decode("utf-8", errors="ignore")


def rgb(im: Image.Image) -> Image.Image:
    """RGB with transparency flattened onto white (a plain convert turns it black)."""
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P") and (im.mode != "P" or "transparency" in im.info):
        im = im.convert("RGBA")
        return Image.alpha_composite(Image.new("RGBA", im.size, "white"), im).convert("RGB")
    return im.convert("RGB")


def load_image(path: Path, thumb: Path) -> Image.Image | None:
    try:
        im = Image.open(path)
        im.draft("RGB", (1024, 1024))
        im = rgb(im)
        im.thumbnail((1024, 1024))
        if not thumb.exists():
            t = im.copy()
            t.thumbnail((256, 256))
            t.save(thumb, "JPEG", quality=82)
        return im
    except Exception:
        return None


def describe(f: File) -> str:
    """How a text-only model sees an image."""
    return f"image {f.rel.rsplit('/', 1)[-1]} in folder {f.rel.rsplit('/', 1)[0] if '/' in f.rel else '.'}"


class OpenCLIP:
    def __init__(self, spec, device):
        import open_clip
        import torch

        arch, pretrained = spec.split("/")
        self.torch, self.device = torch, device
        m, _, self.prep = open_clip.create_model_and_transforms(arch, pretrained=pretrained)
        self.tok = open_clip.get_tokenizer(arch)
        self.model = m.to(device).eval().half()
        self.sees_images = True

    def images(self, ims):
        with self.torch.no_grad():
            return self.model.encode_image(self.torch.stack([self.prep(i) for i in ims]).to(self.device).half()).float().cpu().numpy()

    def texts(self, docs):
        # the text tower sees ~77 tokens: embed 50-word chunks and average them
        pieces, owner = [], []
        for i, d in enumerate(docs):
            w = d.split()
            cs = [" ".join(w[j:j + 50]) for j in range(0, max(len(w), 1), 50)][:16]
            pieces += cs
            owner += [i] * len(cs)
        with self.torch.no_grad():
            E = np.concatenate([self.model.encode_text(self.tok(pieces[j:j + 256]).to(self.device)).float().cpu().numpy()
                                for j in range(0, len(pieces), 256)])
        E /= np.linalg.norm(E, axis=1, keepdims=True)
        owner = np.array(owner)
        return np.stack([E[owner == i].mean(0) for i in range(len(docs))])


class ST:
    def __init__(self, name, device):
        import torch
        from sentence_transformers import SentenceTransformer

        self.m = SentenceTransformer(name, device=device, trust_remote_code=True, model_kwargs={"torch_dtype": torch.float16})
        self.sees_images = "image" in self.m.modalities

    def images(self, ims):
        return self.m.encode(ims, batch_size=16)

    def texts(self, docs):
        return self.m.encode([d[:8000] for d in docs], batch_size=16)


def load(name: str):
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    spec = resolve(name)
    return OpenCLIP(spec[len("open_clip:"):], device) if spec.startswith("open_clip:") else ST(spec, device)


def embed(root: Path, files: list[File], name: str) -> np.ndarray:
    """(n, d) L2-normalized; row i is files[i]. Unreadable images get a text description instead."""
    cdir = root / ".view" / "cache" / f"{slug(name)}-v{CACHE_VERSION}"
    tdir = root / ".view" / "thumbs"
    cdir.mkdir(parents=True, exist_ok=True)
    tdir.mkdir(parents=True, exist_ok=True)
    cache = {}
    if (cdir / "keys.json").exists():
        cache = dict(zip(json.loads((cdir / "keys.json").read_text()), np.load(cdir / "vecs.npy")))
    todo = [f for f in files if key(f) not in cache]
    print(f"\n  {name}  {len(todo):,} to embed · {len(files) - len(todo):,} cached")
    if todo:
        model, t0, done = load(name), time.time(), 0
        with ThreadPoolExecutor(8) as pool:
            for i in range(0, len(todo), 64):
                batch = todo[i:i + 64]
                ims = [f for f in batch if f.kind == "image"] if model.sees_images else []
                loaded = dict(zip(ims, pool.map(lambda f: load_image(root / f.rel, tdir / thumb_name(f)), ims)))
                pics = [f for f in ims if loaded[f] is not None]
                words = [f for f in batch if f not in pics]
                texts = list(pool.map(lambda f: read_text(root / f.rel) if f.kind == "text" else describe(f), words))
                if pics:
                    cache.update(zip(map(key, pics), np.asarray(model.images([loaded[f] for f in pics]), np.float32)))
                if words:
                    cache.update(zip(map(key, words), np.asarray(model.texts(texts), np.float32)))
                done += len(batch)
                rate = done / (time.time() - t0)
                print(f"\r    {done:,}/{len(todo):,}  {rate:.0f} files/s  ~{(len(todo) - done) / rate:.0f}s left   ", end="", flush=True)
        print()
        ks = list(cache)
        np.save(cdir / "vecs.npy", np.stack([cache[k] for k in ks]))
        (cdir / "keys.json").write_text(json.dumps(ks))
    X = np.stack([cache[key(f)] for f in files]).astype(np.float32)
    return X / np.linalg.norm(X, axis=1, keepdims=True)
