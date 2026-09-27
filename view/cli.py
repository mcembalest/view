"""view <folder>: index text + image files, embed, lay out in 3D and 2D, explore in the browser."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from . import embed as E
from . import scan as S


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(prog="view", description=__doc__)
    ap.add_argument("folder", nargs="?", default=".")
    ap.add_argument("--model", choices=list(E.MODELS), default="mobileclip2",
                    help="mobileclip2: fast (default). qwen3-vl: better text + reads text in images, ~40x slower")
    ap.add_argument("--jev", action="store_true", help="ask TypeSafe Jev the questions in .view/jev.json about every file")
    ap.add_argument("-y", "--yes", action="store_true", help="skip the index confirmation")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        ap.error(f"not a folder: {root}")

    if args.jev:
        from . import jev
        jev.load_env(root)

    def extra(files):
        lines = [f"model     {args.model}", f"stores    {root / '.view'}/  (cache, thumbnails, ignore rules)"]
        if args.jev:
            from . import jev
            toks, cost = jev.estimate(root, files, jev.load_questions(root))
            lines.append(f"jev       ~{toks / 1e6:.1f}M tokens ≈ ${cost:.2f}  · text content + image filenames/EXIF are sent to TypeSafe")
            lines.append(f"          questions: {jev.questions_path(root)}")
        return lines

    files = S.confirm(root, extra, assume_yes=args.yes)
    X = E.embed(root, files, args.model)

    map_dir = root / ".view" / "map" / args.model
    map_dir.mkdir(parents=True, exist_ok=True)
    sig = hashlib.sha1("\n".join(E.key(f) for f in files).encode()).hexdigest()
    sig_path = map_dir / "signature"
    if sig_path.exists() and sig_path.read_text() == sig and (map_dir / "points.bin").exists():
        print("\n  layout  unchanged, reusing")
        meta = json.loads((map_dir / "meta.json").read_text())
    else:
        from .project import cluster, project

        print("\n  layout")
        P3, P2 = project(X)
        layers = cluster(X)
        print(f"    clusters   {', '.join(str(len(set(l) - {-1})) for l in layers)} (fine → coarse)")
        np.concatenate([P3.ravel(), P2.ravel()]).astype(np.float32).tofile(map_dir / "points.bin")
        meta = {
            "root": str(root),
            "model": args.model,
            "items": [item(root, f) for f in files],
            "columns": {"type": [f.kind for f in files]},
        }
        for i, l in enumerate(layers):
            name = "cluster" if len(layers) == 1 else f"cluster · level {i + 1}"
            meta["columns"][name] = [int(v) for v in l]
        sig_path.write_text(sig)

    meta["columns"] = {k: v for k, v in meta["columns"].items() if not k.startswith("jev · ")}
    if args.jev:
        from . import jev
        answers = jev.run(root, files)
        for q in jev.load_questions(root):
            meta["columns"][f"jev · {q}"] = [answers[f.rel].get(q) for f in files]
    (map_dir / "meta.json").write_text(json.dumps(meta))

    from .serve import serve
    serve(root, map_dir, args.port, not args.no_open)


def item(root: Path, f: S.File) -> dict:
    it = {"path": f.rel, "kind": f.kind, "size": f.size, "mtime": int(f.mtime), "thumb": E.thumb_name(f)}
    if f.kind == "text":
        it["snippet"] = E.read_text(root / f.rel)[:600]
    return it


if __name__ == "__main__":
    main()
