"""Optional: ask TypeSafe Jev typed questions about every file. Answers become hover fields and colors.

Jev reads text only: text files send their content; images send name, folder, and EXIF.
Questions live in .view/jev.json in the API's own `questions` shape (https://docs.typesafe.ai/api).
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image

from .embed import key, read_text
from .scan import File

URL = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_MTOK = 0.042
TEXT_CHARS = 6000

DEFAULT_QUESTIONS = {
    "kind": {
        "type": "choice",
        "instructions": "What is this file most likely to be?",
        "criteria": {
            "personal photo": "A photo taken with a camera or phone",
            "screenshot": "A capture of a screen: app, website, social post, chat",
            "diagram or chart": "A figure, plot, diagram, or slide",
            "artwork or illustration": None,
            "notes or writing": "Prose, notes, journal, essay",
            "code or data": None,
            "other": None,
        },
    },
    "work": {"type": "noul", "instructions": "Is this file most likely related to work, research, or study (rather than personal life)?"},
}


def load_env(folder: Path):
    """TYPESAFE_API_KEY from the environment, or a .env in the viewed folder, cwd, or the view repo."""
    for d in (folder, Path.cwd(), Path(__file__).resolve().parent.parent):
        if os.environ.get("TYPESAFE_API_KEY"):
            return
        if (d / ".env").exists():
            for line in (d / ".env").read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "TYPESAFE_API_KEY":
                    os.environ["TYPESAFE_API_KEY"] = v.strip().strip("'\"")


def load_questions(root: Path) -> dict:
    p = root / ".view" / "jev.json"
    if not p.exists():
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(DEFAULT_QUESTIONS, indent=2))
    return json.loads(p.read_text())


def exif(path: Path) -> dict:
    try:
        im = Image.open(path)
        ex = im.getexif()
        tags = {ExifTags.TAGS.get(k, k): v for k, v in {**ex, **ex.get_ifd(0x8769)}.items()}
        out = {"width": im.width, "height": im.height}
        out.update({t: str(tags[t]).strip("\x00 ") for t in ("DateTimeOriginal", "Make", "Model", "Software") if t in tags})
        return out | ({"has_gps": True} if ex.get_ifd(0x8825) else {})
    except Exception:
        return {}


def state(root: Path, f: File) -> dict:
    folder, _, name = f.rel.rpartition("/")
    s = {"filename": name, "folder": folder, "modified": datetime.fromtimestamp(f.mtime).strftime("%Y-%m-%d"), "size_kb": round(f.size / 1024)}
    return s | ({"image_metadata": exif(root / f.rel)} if f.kind == "image" else {"content": read_text(root / f.rel)[:TEXT_CHARS]})


def estimate(files: list[File], questions: dict) -> tuple[int, float]:
    per_q = len(json.dumps(questions)) // 4 + 60
    toks = sum((min(f.size, TEXT_CHARS) if f.kind == "text" else 300) // 4 + per_q for f in files)
    return toks, toks / 1e6 * PRICE_PER_MTOK


def ask(body: dict) -> dict:
    req = urllib.request.Request(URL, json.dumps(body).encode(), {
        "Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}", "Content-Type": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return {k: a[a["type"]] for k, a in json.load(r)["answers"].items()}
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 529) or attempt == 5:
                raise
            time.sleep(2 ** attempt)


def run(root: Path, files: list[File]) -> dict[str, dict]:
    """{file.rel: {question: answer}}, cached per file and question set."""
    questions = load_questions(root)
    qhash = hashlib.sha1(json.dumps(questions, sort_keys=True).encode()).hexdigest()[:10]
    cpath = root / ".view" / "jev_cache.json"
    cache = json.loads(cpath.read_text()) if cpath.exists() else {}
    ck = lambda f: f"{key(f)}|{qhash}"
    todo = [f for f in files if ck(f) not in cache]
    print(f"\n  jev  {len(todo):,} to ask · {len(files) - len(todo):,} cached · questions: {', '.join(questions)}")

    def one(f):
        try:
            cache[ck(f)] = ask({"model": "jev-latest", "state": state(root, f), "questions": questions})
        except Exception as e:
            print(f"\n    jev failed on {f.rel}: {e}", file=sys.stderr)

    with ThreadPoolExecutor(16) as pool:
        for n, _ in enumerate(pool.map(one, todo), 1):
            print(f"\r    {n:,}/{len(todo):,}", end="", flush=True)
    if todo:
        print()
        cpath.write_text(json.dumps(cache))
    return {f.rel: cache.get(ck(f), {}) for f in files}
