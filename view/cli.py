"""view <folder>: see a folder's text + image files as a semantic map, in 3D and 2D."""

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
    ap.add_argument("-m", "--model", action="append",
                    help="embedding model; repeat to build several maps and flip between them. "
                         f"Any sentence-transformers id, open_clip:ARCH/PRETRAINED, or an alias: {', '.join(E.ALIASES)} "
                         "(default mobileclip2)")
    ap.add_argument("--jev", action="store_true", help="ask TypeSafe Jev the questions in .view/jev.json about every file")
    ap.add_argument("-y", "--yes", action="store_true", help="skip the index confirmation")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    models = args.model or ["mobileclip2"]

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        ap.error(f"not a folder: {root}")
    if args.jev:
        from . import jev
        jev.load_env(root)

    def extra(files):
        lines = [f"models    {', '.join(models)}", f"writes    {root / '.view'}/"]
        if args.jev:
            from . import jev
            toks, cost = jev.estimate(files, jev.load_questions(root))
            lines.append(f"jev       ~{toks / 1e6:.1f}M tokens ≈ ${cost:.2f}; sends text content and image names/EXIF to TypeSafe")
        return lines

    files = S.confirm(root, extra, assume_yes=args.yes)
    out = root / ".view" / "map"
    out.mkdir(parents=True, exist_ok=True)
    sig = hashlib.sha1("\n".join(E.key(f) for f in files).encode()).hexdigest()
    vectors = {}
    for m in models:
        X = vectors[m] = E.embed(root, files, m)
        s = E.slug(m)
        if (out / f"{s}.sig").exists() and (out / f"{s}.sig").read_text() == sig:
            continue
        from .project import cluster, project

        P3, P2 = project(X)
        layers, dupes = cluster(X)
        print(f"    clusters   {' / '.join(str(len(set(l) - {-1})) for l in layers)}")
        np.concatenate([P3.ravel(), P2.ravel()]).astype(np.float32).tofile(out / f"{s}.bin")
        (out / f"{s}.json").write_text(json.dumps({"clusters": [[int(v) for v in l] for l in layers], "duplicates": dupes}))
        (out / f"{s}.sig").write_text(sig)

    meta = {
        "root": str(root),
        "models": [{"name": m, "slug": E.slug(m)} for m in models],
        "items": [[f.rel, f.kind, f.size, int(f.mtime)] for f in files],
        "columns": {},
    }
    if args.jev:
        from . import jev
        answers = jev.run(root, files)
        for q in jev.load_questions(root):
            meta["columns"][f"jev · {q}"] = [answers[f.rel].get(q) for f in files]
    (out / "meta.json").write_text(json.dumps(meta))

    from .serve import serve
    serve(root, files, models, vectors, args.port, not args.no_open)


if __name__ == "__main__":
    main()
