"""Optional: ask TypeSafe's Jev typed questions about every file (text only, so images are described
by filename, folder and EXIF metadata). Answers become hover fields and color-by options.

Questions live in .view/jev.json, in the same shape as the HTTP API's `questions` map.
"""

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image

from .embed import key, read_text
from .scan import File

PRICE_PER_MTOK = 0.042
TEXT_STATE_CHARS = 6000
CONCURRENCY = 16

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
    "work": {
        "type": "noul",
        "instructions": "Is this file most likely related to work, research, or study (rather than personal life)?",
    },
}


def load_env(start: Path):
    """Read TYPESAFE_API_KEY from a .env next to the viewed folder, the cwd, or the view repo."""
    if os.environ.get("TYPESAFE_API_KEY"):
        return
    for d in (start, Path.cwd(), Path(__file__).resolve().parent.parent):
        env = d / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "TYPESAFE_API_KEY":
                    os.environ["TYPESAFE_API_KEY"] = v.strip().strip("'\"")
                    return


def questions_path(root: Path) -> Path:
    return root / ".view" / "jev.json"


def load_questions(root: Path) -> dict:
    p = questions_path(root)
    if not p.exists():
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(DEFAULT_QUESTIONS, indent=2))
    return json.loads(p.read_text())


def exif(path: Path) -> dict:
    try:
        im = Image.open(path)
        out = {"width": im.width, "height": im.height}
        ex = im.getexif()
        tags = {ExifTags.TAGS.get(k, k): v for k, v in ex.items()}
        tags.update({ExifTags.TAGS.get(k, k): v for k, v in ex.get_ifd(0x8769).items()})
        for t in ("DateTimeOriginal", "Make", "Model", "Software", "LensModel"):
            if t in tags:
                out[t] = str(tags[t]).strip("\x00 ")
        if ex.get_ifd(0x8825):
            out["has_gps"] = True
        return out
    except Exception:
        return {}


def state(root: Path, f: File) -> dict:
    s = {
        "filename": f.rel.rsplit("/", 1)[-1],
        "folder": f.rel.rsplit("/", 1)[0] if "/" in f.rel else "",
        "file_type": f.ext,
        "modified": datetime.fromtimestamp(f.mtime).strftime("%Y-%m-%d"),
        "size_kb": round(f.size / 1024),
    }
    if f.kind == "image":
        s["image_metadata"] = exif(root / f.rel)
    else:
        s["content"] = read_text(root / f.rel)[:TEXT_STATE_CHARS]
    return s


def estimate(root: Path, files: list[File], questions: dict) -> tuple[int, float]:
    q = len(json.dumps(questions)) // 4
    toks = sum((min(f.size, TEXT_STATE_CHARS) if f.kind == "text" else 300) // 4 + q + 60 for f in files)
    return toks, toks / 1e6 * PRICE_PER_MTOK


def _question(d: dict):
    import typesafe_sdk as ts

    d = dict(d)
    return {"choice": ts.Choice, "noul": ts.Noul, "score": ts.Score}[d.pop("type")](**d)


def _answer(a) -> float | str:
    return {"choice": lambda: a.choice, "noul": lambda: round(a.noul, 3), "score": lambda: round(a.score, 3)}[a.type]()


def run(root: Path, files: list[File]) -> dict[str, dict]:
    """Return {file.rel: {question: answer}}; cached by file key + question set."""
    import typesafe_sdk as ts

    questions = load_questions(root)
    qhash = hashlib.sha1(json.dumps(questions, sort_keys=True).encode()).hexdigest()[:10]
    cpath = root / ".view" / "jev_cache.json"
    cache = json.loads(cpath.read_text()) if cpath.exists() else {}
    todo = [f for f in files if f"{key(f)}|{qhash}" not in cache]
    print(f"\n  jev  {len(todo):,} new · {len(files) - len(todo):,} cached   questions: {', '.join(questions)}")

    async def go():
        sem, done = asyncio.Semaphore(CONCURRENCY), 0
        qs = {k: _question(v) for k, v in questions.items()}
        async with ts.AsyncTypeSafeClient() as client:
            async def one(f):
                nonlocal done
                async with sem:
                    try:
                        r = await client.system_one(state=state(root, f), questions=qs)
                        cache[f"{key(f)}|{qhash}"] = {k: _answer(a) for k, a in r.answers.items()}
                    except ts.TypeSafeError as e:
                        print(f"\n    jev failed on {f.rel}: {e}", file=sys.stderr)
                    done += 1
                    print(f"\r    {done:,}/{len(todo):,}", end="", flush=True)

            await asyncio.gather(*(one(f) for f in todo))
        print()

    if todo:
        asyncio.run(go())
        cpath.write_text(json.dumps(cache))
    return {f.rel: cache.get(f"{key(f)}|{qhash}", {}) for f in files}
