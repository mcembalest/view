# view

Point at a folder, see its files as a semantic map, and swap between 3D and 2D.

```sh
view ~/Documents/first/pics
```

1. **Scan.** It shows exactly what will be indexed (counts by type, extension, top folders) and everything it will skip and why. Nothing happens until you press enter. Press `e` to edit `.view/ignore` (gitignore syntax) and it rescans; loop until it looks right. Press `l` to list every file.
2. **Embed.** Each file becomes one point. Images and text share one embedding space (MobileCLIP2). Vectors are cached per file, so re-runs only embed what changed.
3. **Lay out.** UMAP to 3D, then 2D, aligned so toggling is a short, continuous move. EVōC clusters for color.
4. **Explore** at `127.0.0.1`. Drag to orbit, press space for 2D/3D, hover for a preview, click a point to pin it, then use Reveal in Finder / Open.

## Install

```sh
uv tool install -e ~/Desktop/repos/view   # puts `view` on PATH (shadows vim's `view`)
```

## Options

| flag | |
|---|---|
| `--jev` | Ask [TypeSafe Jev](https://docs.typesafe.ai) the typed questions in `.view/jev.json` about every file. Answers show on hover and in "color by". Jev is text-only, so images are described by filename, folder, and EXIF; text files send their first 6k characters. The confirmation screen shows the estimated cost first (~$0.01 per 1k images). Reads `TYPESAFE_API_KEY` from the env or a `.env`. |
| `--model qwen3-vl` | Qwen3-VL-Embedding-2B: better on long text, reads text inside screenshots, ~40x slower. |
| `-y` | Skip the confirmation. |
| `--port`, `--no-open` | |

Everything view writes goes in `<folder>/.view/`: ignore rules, Jev questions, embedding cache, thumbnails, layout.

**Indexed types.** Images: png jpg jpeg gif webp bmp tiff heic. Text: txt md rst org tex csv tsv json jsonl yaml toml html xml, common code files, ipynb. Hidden files/folders, `node_modules`, empty files, and files over 50MB are skipped. Current scale target: ≤100k files.

## Why these choices

All benchmarks ran on an M3 Max (MPS). The test corpus was 2k 20 Newsgroups posts (20 topics) + 2k Imagenette photos (10 classes), plus a real ~1k-screenshot folder for qualitative checks.

**Embedding model.** "knn" = share of each file's 10 nearest neighbors with the same label.

| model | text/s | img/s | knn text | knn img | license |
|---|---|---|---|---|---|
| **MobileCLIP2-S4** (default) | 351 | 81 | 0.41 (0.44 chunked) | 0.995 | Apple AMLR |
| SigLIP2 so400m-384 | 147 | 14 | 0.35 | 0.995 | Apache 2 |
| jina-clip-v2 | 23 | 5 | 0.51 | 0.987 | CC-BY-NC |
| Qwen3-VL-Embedding-2B | 4 | 6 (1.4 on screenshots) | 0.52 | 0.997 | Apache 2 |

- **Speed.** MobileCLIP2 is 15–60x faster than the alternatives with equal image quality. At that rate 100k files embed in about 20 min instead of hours.
- **Text.** Its text tower sees 77 tokens, so text files are embedded as the mean of 50-word chunks (0.39 → 0.44).
- **Modality gap.** In every model tested, text and images never land among each other's nearest neighbors. They form separate continents on the map.

**Projection.** 3D uses UMAP 0.6 (Leland McInnes' new recursive init + Adam optimizer path, pinned from the `0.6dev` branch). For 2D, four options were compared:

| 2D from 3D | knn | trustworthiness | movement on toggle |
|---|---|---|---|
| PCA of 3D (pure camera flatten) | 0.742 | 0.959 | 0 |
| **UMAP on embeddings, init = top-down view of 3D** | 0.754 | 0.966 | 0.28 |
| UMAP on the 3D coordinates | 0.755 | 0.963 | 0.49 (stringy artifacts) |
| independent 2D UMAP | 0.753 | 0.967 | 0.58 |

The chosen method keeps full 2D quality while points move half as far as with an independent layout. At 100k points, 3D + 2D take ~17s.

**Clusters.** [EVōC](https://github.com/TutteInstitute/evoc), McInnes' embedding-native successor to UMAP+HDBSCAN clustering, gives multi-granularity layers (~2s at 100k).

**Renderer.** three.js, raw WebGL2, and deck.gl all hold 60fps at 100k points. three.js was the least code for orbit/pan/zoom plus a custom camera swing between perspective and top-down, and it needs no build step (vendored ES module).
