#!/usr/bin/env python3
"""Generate pseudo-Live2D assets for the homepage mascot (kabibichan).

Input : static/images/kabibichan.png  (flat chibi illustration, transparent bg)
Output: static/images/kabibi/base.png          trimmed + downscaled sprite
        static/images/kabibi/lid_{L,R}.png     "eyelid" patches (eye replaced by skin)
        static/images/kabibi/line_{L,R}.png    closed-eye lash arcs
        static/images/kabibi/manifest.json     geometry used by static/js/kabibi-live2d.js

Re-run this script after updating the artwork:  python3 scripts/make_kabibi_assets.py
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "static/images/kabibichan.png"
OUT_DIR = ROOT / "static/images/kabibi"

OUT_HEIGHT = 800      # height of the web sprite (mascot renders ~210 CSS px)
PAD = 24              # padding kept around the silhouette
DILATE = 4            # px, eyelid patch margin around the eye shape
FEATHER = 1.2         # px, alpha feathering of the eyelid patch

# Landmark rows in the *source* image, read off the artwork (see scripts docs).
ANCHORS_SRC = {
    "headTop": 150,
    "hairTip": 1330,
    "eyeTop": 915,
    "eyeBottom": 1120,
    "chin": 1285,
    "neck": 1330,
    "shoulder": 1420,
    "waist": 1585,
    "skirtTop": 1580,
    "skirtBottom": 1735,
    "legsBottom": 1900,
    "feet": 1970,
    "axisX": 1032,
}


def load_sprite() -> tuple[Image.Image, tuple[int, int]]:
    """Return the sprite trimmed to its silhouette bbox, plus the trim offset."""
    im = Image.open(SRC).convert("RGBA")
    alpha = np.array(im)[..., 3]
    ys, xs = np.nonzero(alpha > 8)
    x0, y0 = int(xs.min()), int(ys.min())
    x1, y1 = int(xs.max()) + 1, int(ys.max()) + 1
    off = (max(0, x0 - PAD), max(0, y0 - PAD))
    box = (off[0], off[1], min(im.width, x1 + PAD), min(im.height, y1 + PAD))
    return im.crop(box), off


def resize_premul_arr(arr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Downscale a float RGBA array (0..1) with premultiplied alpha.

    Doing the LANCZOS pass on straight RGBA drags black RGB values out of
    fully-transparent pixels, which shows up as a dark fringe on the sprite.
    """
    bands = [
        np.asarray(
            Image.fromarray(ch.astype(np.float32), mode="F").resize(size, Image.LANCZOS),
            dtype=np.float64,
        )
        for ch in np.transpose(arr, (2, 0, 1))
    ]
    p = np.stack(bands, axis=-1)
    rgb = np.where(p[..., 3:] > 1e-4, p[..., :3] / np.maximum(p[..., 3:], 1e-6), 0.0)
    return np.clip(np.concatenate([rgb, p[..., 3:]], axis=-1) * 255.0, 0, 255)


def resize_premul(im: Image.Image, height: int) -> Image.Image:
    """Downscale an RGBA image to `height`, keeping edges clean."""
    w, h = im.size
    nw = max(1, round(w * height / h))
    arr = np.asarray(im).astype(np.float64) / 255.0
    return Image.fromarray(resize_premul_arr(arr, (nw, height)).astype(np.uint8), "RGBA")


def find_eyes(im: Image.Image, off: tuple[int, int]) -> list[np.ndarray]:
    """Locate the two maroon eye shapes as binary masks (source coords)."""
    a = np.array(im).astype(np.int32)[..., :3]
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    maroon = (r > 40) & (r < 200) & (g < 70) & (b < 90) & ((r - g) > 35)

    y0 = ANCHORS_SRC["eyeTop"] - 40 - off[1]
    y1 = ANCHORS_SRC["eyeBottom"] + 40 - off[1]
    x0 = max(0, 600 - off[0])
    x1 = min(im.width, 1400 - off[0])
    band = np.zeros_like(maroon)
    band[max(0, y0):y1, x0:x1] = True
    mask = maroon & band

    labels, n = ndimage.label(mask)
    if n < 2:
        raise SystemExit("could not locate the eyes; check the artwork")
    sizes = ndimage.sum(mask, labels, range(1, n + 1))
    order = np.argsort(sizes)[::-1]
    eyes = []
    for idx in order[:2]:
        blob = labels == (idx + 1)
        if sizes[idx] < 4000:
            raise SystemExit("eye detection looks wrong; check the artwork")
        eyes.append(blob)
    # viewer-left first
    eyes.sort(key=lambda m: np.nonzero(m)[1].mean())
    return eyes


def _jacobi(fill: np.ndarray, mask: np.ndarray, iters: int) -> np.ndarray:
    """Average neighbours inside `mask` (harmonic diffusion, edge-padded)."""
    for _ in range(iters):
        pad = np.pad(fill, ((1, 1), (1, 1), (0, 0)), mode="edge")
        avg = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:]) / 4.0
        fill[mask] = avg[mask]
    return fill


def inpaint_skin(rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Replace `hole` pixels with a smooth continuation of the surrounding skin.

    Coarse-to-fine harmonic diffusion: a plain Jacobi sweep propagates one
    pixel per pass, which leaves the colour of features inside the hole (the
    eyebrows, the eye highlight) as a visible smudge.
    """
    ys, xs = np.nonzero(hole)
    pad = 28
    y0 = max(0, ys.min() - pad); y1 = min(rgb.shape[0], ys.max() + pad + 1)
    x0 = max(0, xs.min() - pad); x1 = min(rgb.shape[1], xs.max() + pad + 1)
    sub, hsub = rgb[y0:y1, x0:x1], hole[y0:y1, x0:x1]

    # Seed with the nearest known colour, then diffuse on a 1/4 grid.
    _, (iy, ix) = ndimage.distance_transform_edt(hsub, return_indices=True)
    seed = sub[iy, ix]
    seed[hsub] = sub[iy, ix][hsub]

    cw, ch = max(2, sub.shape[1] // 4), max(2, sub.shape[0] // 4)
    coarse_mask = ndimage.maximum_filter(hsub, size=4)[::4, :cw][:ch]
    small = Image.fromarray(seed.astype(np.uint8)).resize((cw, ch), Image.BOX)
    coarse = np.asarray(small).astype(np.float64)
    coarse = _jacobi(coarse, coarse_mask, 600)

    up = np.asarray(
        Image.fromarray(coarse.astype(np.uint8)).resize((sub.shape[1], sub.shape[0]), Image.BICUBIC)
    ).astype(np.float64)
    fill = seed.copy()
    fill[hsub] = up[hsub]
    fill = _jacobi(fill, hsub, 80)

    out = rgb.copy()
    out[y0:y1, x0:x1][hsub] = fill[hsub]
    return out


def closed_lid_mask(
    hole: np.ndarray, color: np.ndarray
) -> tuple[Image.Image, tuple[int, int, int, int], float]:
    """Render a closed-eye lash arc for one eye region (drawn supersampled)."""
    ys, xs = np.nonzero(hole)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    w, h = x1 - x0 + 1, y1 - y0 + 1
    pad = max(12, int(0.08 * max(w, h)))
    ss = 3  # supersampling factor, keeps the arc smooth after downscale
    img = Image.new("RGBA", ((w + 2 * pad) * ss, (h + 2 * pad) * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Arc following the lower lid, sitting where the eye would shut.
    rx = 0.42 * w * ss
    ry = 0.17 * h * ss
    bottom = (y1 - 0.04 * h - y0 + pad) * ss
    cy = bottom - ry
    cx = ((x0 + x1) / 2 - x0 + pad) * ss
    bbox = [cx - rx, cy - ry, cx + rx, cy + ry]
    width = max(6, round(0.085 * h * ss))
    col = tuple(int(v) for v in color) + (255,)
    d.arc(bbox, start=18, end=162, fill=col, width=width)
    for ang in (18, 162):
        ex = cx + rx * np.cos(np.deg2rad(ang))
        ey = cy + ry * np.sin(np.deg2rad(ang))
        rr = width / 2
        d.ellipse([ex - rr, ey - rr, ex + rr, ey + rr], fill=col)

    box = (x0 - pad, y0 - pad, x1 + pad + 1, y1 + pad + 1)
    # Centroid of the drawn arc, so the renderer can slide it with the lid.
    drawn = np.asarray(img)[..., 3] > 8
    arc_cy = (box[1] + np.nonzero(drawn)[0].mean() / ss) if drawn.any() else (y0 + y1) / 2
    return img, box, float(arc_cy)


def feather_alpha(mask: np.ndarray, px: float) -> np.ndarray:
    """Soft alpha edge so the patch does not leave a hard seam."""
    a = mask.astype(np.float64)
    a = ndimage.gaussian_filter(a, px)
    return np.clip(a * 255, 0, 255).astype(np.uint8)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    trimmed, off = load_sprite()
    scale = OUT_HEIGHT / trimmed.height
    base = resize_premul(trimmed, OUT_HEIGHT)
    base.save(OUT_DIR / "base.png", optimize=True)

    full = np.array(trimmed).astype(np.float64)
    rgb_full = full[..., :3]

    manifest = {
        "src": "base.png",
        "w": base.width,
        "h": base.height,
        "eyes": [],
        "anchors": {},
    }

    for name, blob in zip(("L", "R"), find_eyes(trimmed, off)):
        hole = ndimage.binary_fill_holes(blob)
        hole = ndimage.binary_dilation(hole, iterations=DILATE)
        ys, xs = np.nonzero(hole)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1

        # Dark maroon outline colour of the eye for the closed lid line.
        eye_px = rgb_full[blob]
        dark = eye_px[(eye_px[:, 0] < 100) & (eye_px[:, 1] < 40)]
        color = np.median(dark if len(dark) > 50 else eye_px, axis=0)

        fill = inpaint_skin(rgb_full, hole)
        patch = np.zeros((full.shape[0], full.shape[1], 4), dtype=np.float64)
        patch[..., :3] = fill / 255.0
        patch[..., 3] = feather_alpha(hole, FEATHER) / 255.0

        mx0, my0 = max(0, x0 - 2), max(0, y0 - 2)
        mx1, my1 = min(full.shape[1], x1 + 2), min(full.shape[0], y1 + 2)
        lid_arr = resize_premul_arr(
            patch[my0:my1, mx0:mx1],
            (max(1, round((mx1 - mx0) * scale)), max(1, round((my1 - my0) * scale))),
        )
        Image.fromarray(lid_arr.astype(np.uint8), "RGBA").save(
            OUT_DIR / f"lid_{name}.png", optimize=True
        )

        line_img, line_box, arc_cy = closed_lid_mask(blob, color)
        line_arr = resize_premul_arr(
            np.asarray(line_img).astype(np.float64) / 255.0,
            (
                max(1, round((line_box[2] - line_box[0]) * scale)),
                max(1, round((line_box[3] - line_box[1]) * scale)),
            ),
        )
        line_img = Image.fromarray(line_arr.astype(np.uint8), "RGBA")
        line_img.save(OUT_DIR / f"line_{name}.png", optimize=True)

        manifest["eyes"].append({
            "lid": {
                "src": f"lid_{name}.png",
                "x": round(mx0 * scale, 2), "y": round(my0 * scale, 2),
                "w": int(lid_arr.shape[1]), "h": int(lid_arr.shape[0]),
            },
            "line": {
                "src": f"line_{name}.png",
                "x": round(line_box[0] * scale, 2), "y": round(line_box[1] * scale, 2),
                "w": line_img.width, "h": line_img.height,
                "arcC": round(arc_cy * scale, 2),
            },
            "top": round(y0 * scale, 2),
            "bottom": round(y1 * scale, 2),
        })

    for k, v in ANCHORS_SRC.items():
        if k == "axisX":
            manifest["anchors"][k] = round((v - off[0]) * scale, 2)
        else:
            manifest["anchors"][k] = round((v - off[1]) * scale, 2)
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"base {base.width}x{base.height}  eyes={len(manifest['eyes'])}")
    for e in manifest["eyes"]:
        print("  lid", json.dumps(e["lid"], ensure_ascii=False),
              "line", json.dumps(e["line"], ensure_ascii=False))
    print("anchors", json.dumps(manifest["anchors"], ensure_ascii=False))


if __name__ == "__main__":
    main()
