"""Find the files to index, show the proposed index, and let the user veto it until it looks right."""

import os
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pathspec

IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif"}
TEXT_EXTS = {
    "txt", "md", "markdown", "rst", "org", "tex", "csv", "tsv", "json", "jsonl", "yaml", "yml", "toml",
    "html", "htm", "xml", "py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "c", "h", "cpp", "swift",
    "rb", "sh", "sql", "ipynb",
}
MAX_BYTES = 50_000_000

DEFAULT_IGNORE = """\
# Files matching these patterns are left out of the index (gitignore syntax).
# Paths are relative to the folder being viewed. Save and close to rescan.
#
# examples:
#   drafts/
#   *.json
#   Screenshot*
"""


@dataclass(frozen=True)
class File:
    rel: str
    kind: str  # "image" | "text"
    ext: str
    size: int
    mtime: float


def ignore_path(root: Path) -> Path:
    return root / ".view" / "ignore"


def count_files(path: str) -> int:
    return sum(len(fs) for _, _, fs in os.walk(path))


def scan(root: Path) -> tuple[list[File], Counter]:
    """Walk root; return files to index and a count of skipped files by reason."""
    ip = ignore_path(root)
    spec = pathspec.GitIgnoreSpec.from_lines(ip.read_text().splitlines() if ip.exists() else [])
    files, skipped = [], Counter()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        kept = []
        for d in sorted(dirnames):
            rel = d if rel_dir == "." else f"{rel_dir}/{d}"
            if rel == ".view":
                continue
            if d.startswith(".") or d == "node_modules":
                skipped[f"in hidden/vendored folder  {rel}/"] += count_files(os.path.join(dirpath, d))
            elif spec.match_file(rel + "/"):
                skipped[f"in ignored folder  {rel}/"] += count_files(os.path.join(dirpath, d))
            else:
                kept.append(d)
        dirnames[:] = kept
        for f in sorted(filenames):
            rel = f if rel_dir == "." else f"{rel_dir}/{f}"
            ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
            if f.startswith("."):
                skipped["hidden file"] += 1
                continue
            if spec.match_file(rel):
                skipped["matched .view/ignore"] += 1
                continue
            kind = "image" if ext in IMAGE_EXTS else "text" if ext in TEXT_EXTS else None
            if kind is None:
                skipped[f".{ext or '(no extension)'}  not a text or image type"] += 1
                continue
            st = os.stat(os.path.join(dirpath, f))
            if st.st_size == 0 or st.st_size > MAX_BYTES:
                skipped["empty or over 50MB"] += 1
                continue
            files.append(File(rel, kind, ext, st.st_size, st.st_mtime))
    return files, skipped


def summarize(root: Path, files: list[File], skipped: Counter, extra: list[str]) -> str:
    by_kind = Counter(f.kind for f in files)
    lines = [f"\n  view  {root}\n", f"  will index  {len(files):,} files"]
    for kind in ("image", "text"):
        if by_kind[kind]:
            exts = Counter(f.ext for f in files if f.kind == kind).most_common()
            lines.append(f"    {kind:<6} {by_kind[kind]:>7,}   " + " · ".join(f"{e} {n:,}" for e, n in exts))
    where = Counter(f.rel.split("/")[0] + "/" if "/" in f.rel else "(top level)" for f in files).most_common()
    lines.append("    where    " + " · ".join(f"{d} {n:,}" for d, n in where[:8]) + (f" · {len(where) - 8} more folders" if len(where) > 8 else ""))
    if skipped:
        lines.append(f"\n  skipping  {sum(skipped.values()):,} files")
        for reason, n in skipped.most_common(10):
            lines.append(f"    {n:>7,}   {reason}")
        if len(skipped) > 10:
            lines.append(f"    … and {len(skipped) - 10} more reasons")
    lines += [""] + [f"  {x}" for x in extra]
    return "\n".join(lines)


def confirm(root: Path, extra_fn, assume_yes: bool = False) -> list[File]:
    """Show the proposed index; loop until the user accepts it. extra_fn(files) -> extra summary lines."""
    ip = ignore_path(root)
    if not ip.exists():
        ip.parent.mkdir(exist_ok=True)
        ip.write_text(DEFAULT_IGNORE)
    while True:
        files, skipped = scan(root)
        print(summarize(root, files, skipped, extra_fn(files)))
        if assume_yes:
            return files
        if not files:
            print("  nothing to index.")
        ans = input("\n  [enter] index these   [l] list files   [e] edit ignore rules   [q] quit  › ").strip().lower()
        if ans == "" and files:
            return files
        if ans == "q":
            raise SystemExit(0)
        if ans == "l":
            pager = os.environ.get("PAGER", "less")
            body = "\n".join(f"{f.kind:<6} {f.rel}" for f in files)
            subprocess.run([pager], input=body.encode()) if pager else print(body)
        if ans == "e":
            subprocess.run([os.environ.get("EDITOR", "nano"), str(ip)])
