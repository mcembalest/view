# view

Point at a folder, see its files as a semantic map, and swap between 3D and 2D.

```sh
view ~/Documents/first/pics
view ~/notes -m mobileclip2 -m qwen3-text -m minilm   # several models: flip between them in the browser
```

## What happens

1. **Scan.** It shows exactly what will be indexed (counts by type, extension, and folder) and everything it will skip and why. Nothing runs until you press enter. Press `e` to edit `.view/ignore` (gitignore syntax) and it rescans; loop until it looks right. Press `l` to list every file.
2. **Embed.** Each file becomes one point, embedded with each model you asked for. Vectors are cached per file, so re-runs only embed what changed.
3. **Lay out.** UMAP to 3D, then 2D, aligned so toggling is a short, continuous move. EVōC gives clusters at several granularities and near-duplicate groups.
4. **Explore** in the browser.

## In the browser

Everything narrows one thing, **the set**. The map lights the set, and the sidebar shows it as a thumbnail grid.

| to… | do |
|---|---|
| filter by name or folder | type in the box (`screenshot 2025 -pasted`: all words must match; `-word` excludes) |
| find by meaning | type a description, press ⏎ (top 100, ranked) |
| color / group by anything | `color`: clusters (each model, each level), near-duplicates, type, extension, folder, parent folder, month, size, Jev answers |
| isolate groups | click a legend row; shift-click to add or remove more |
| grab a region | shift-drag on the map |
| peek | hover a point, or a grid tile |
| act on a file | click a point or tile to pin it, then Reveal in Finder / Open |
| act on the set | copy paths |
| compare models | switch model; points glide to the new layout and keep their colors, so you can watch one model's clusters land in another |
| 2D / 3D | `space` |

The URL keeps the model, 2D/3D, color, and filter, so a reload or bookmark comes back to the same view.

## Models

Anything works:
- **aliases:** `mobileclip2` (default), `siglip2`, `clip`, `qwen3-vl`, `minilm`, `qwen3-text`
- **any open_clip model:** `open_clip:ARCH/PRETRAINED`
- **any sentence-transformers model:** a Hugging Face id

Text-only models embed each image from its filename and folder. That's a different, and sometimes useful, view of the same files.

There's no "best" model here; compare them by looking. Some things seen so far:
- **MobileCLIP2** is fast (~80 images/s on an M3 Max) and groups images by what they look like. Its text tower reads only 77 tokens, so text files are embedded as the mean of 50-word chunks.
- **Qwen3-VL-Embedding** reads text inside screenshots and groups by concept, but runs ~1.4 images/s here.
- **In every multimodal model tried,** text files and images form separate continents (the modality gap). Search by meaning therefore ranks within each type.

## Jev (optional)

`--jev` asks [TypeSafe Jev](https://docs.typesafe.ai) the typed questions in `.view/jev.json` (API format) about every file. Answers show on hover and in `color`.
- **What it reads:** Jev is text-only, so images are described by filename, folder, and EXIF; text files send their first 6k characters.
- **Cost:** the scan screen shows an estimate first (~$0.01 per 1k images).
- **API key:** `TYPESAFE_API_KEY` from the environment or a `.env`.

## Install

```sh
uv tool install -e ~/Desktop/repos/view   # puts `view` on PATH (shadows vim's `view`)
```

Everything view writes goes in `<folder>/.view/`: ignore rules, Jev questions, caches, thumbnails, layouts. Current scale target: ≤100k files.

**Indexed types:**
- **Images:** png, jpg, gif, webp, bmp, tiff, heic.
- **Text:** txt, md, rst, org, tex, csv, json, yaml, toml, html, xml, common code files, ipynb.

Hidden files and folders, `node_modules`, empty files, and files over 50MB are skipped.

## Pieces

- **Layout:** UMAP 0.6 (Leland McInnes' recursive init + Adam optimizer, pinned from `0.6dev`). 3D comes first; 2D is initialized from the top-down view of 3D, then rotated to match it.
- **Clusters:** [EVōC](https://github.com/TutteInstitute/evoc).
- **Viewer:** three.js with no build step. 60fps at 100k points on an M3 Max.
