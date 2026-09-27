"""Embeddings -> 3D layout -> 2D layout, aligned so toggling between them is a small, smooth move.

Benchmarks on a labeled text+image corpus (see README) picked:
  3D: UMAP 0.6 (recursive init + Adam optimizer), cosine metric
  2D: UMAP on the same embeddings, initialized from the top-down view of the 3D layout
      (the 3D layout rotated onto its principal axes and viewed down z), then rotated to match it.
That keeps 2D quality equal to an independent 2D UMAP while points move ~half as far on toggle.
"""

import time
import warnings

import numpy as np

warnings.filterwarnings("ignore", module="umap")
warnings.filterwarnings("ignore", module="sklearn")


def _umap(n_components, init, seed):
    import umap

    return umap.UMAP(n_components=n_components, metric="cosine", init=init, optimizer="adam",
                     compatibility_layout=False, random_state=seed)


def _normalize(P):
    P = P - P.mean(0)
    return P / np.abs(P).max() * 10  # fit in [-10, 10]


def project(X: np.ndarray, seed: int | None = 0) -> tuple[np.ndarray, np.ndarray]:
    n = len(X)
    if n < 5:
        rng = np.random.default_rng(0)
        P3 = rng.normal(size=(n, 3))
        return _normalize(P3), _normalize(P3[:, :2])
    t = time.time()
    P3 = _umap(3, "recursive", seed).fit_transform(X)
    # rotate onto principal axes: looking down z shows the widest spread (the 2D starting point)
    P3 = P3 - P3.mean(0)
    _, _, Vt = np.linalg.svd(P3, full_matrices=False)
    P3 = _normalize(P3 @ Vt.T)
    print(f"    3D layout  {time.time() - t:.1f}s")
    t = time.time()
    init = P3[:, :2] / P3[:, :2].std()
    P2 = _umap(2, init, seed).fit_transform(X)
    # Procrustes: rotate/reflect the 2D result back onto its initialization so nothing spins on toggle
    A = P2 - P2.mean(0)
    U, _, Vt = np.linalg.svd(A.T @ P3[:, :2])
    P2 = A @ (U @ Vt)
    P2 *= P3[:, :2].std() / P2.std()  # same scale as the top-down view of 3D
    print(f"    2D layout  {time.time() - t:.1f}s")
    return P3.astype(np.float32), P2.astype(np.float32)


def cluster(X: np.ndarray) -> list[np.ndarray]:
    """EVoC (McInnes) multi-granularity clusters, finest first. -1 = noise."""
    if len(X) < 20:
        return [np.zeros(len(X), dtype=int)]
    import evoc

    c = evoc.EVoC(random_state=0)
    c.fit_predict(X)
    return [np.asarray(layer) for layer in c.cluster_layers_]
