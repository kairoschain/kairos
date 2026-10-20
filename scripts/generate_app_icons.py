#!/usr/bin/env python3

"""Generate deterministic Kairos application icons without external tools."""

from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "noid_gui" / "assets" / "app-icons"
PNG_SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return float(value >= edge1)
    t = clamp((value - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def rounded_box_distance(x: float, y: float, half: float, radius: float) -> float:
    qx = abs(x) - (half - radius)
    qy = abs(y) - (half - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    inside = min(max(qx, qy), 0.0)
    return outside + inside - radius


def segment_distance(
    px: float,
    py: float,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
) -> float:
    dx = end_x - start_x
    dy = end_y - start_y
    length_squared = dx * dx + dy * dy
    projection = clamp(
        ((px - start_x) * dx + (py - start_y) * dy) / length_squared
    )
    closest_x = start_x + projection * dx
    closest_y = start_y + projection * dy
    return math.hypot(px - closest_x, py - closest_y)


def over(
    base: tuple[float, float, float, float],
    color: tuple[float, float, float],
    alpha: float,
) -> tuple[float, float, float, float]:
    alpha = clamp(alpha)
    base_alpha = base[3]
    output_alpha = alpha + base_alpha * (1.0 - alpha)
    if output_alpha <= 0.0:
        return 0.0, 0.0, 0.0, 0.0
    return (
        (color[0] * alpha + base[0] * base_alpha * (1.0 - alpha)) / output_alpha,
        (color[1] * alpha + base[1] * base_alpha * (1.0 - alpha)) / output_alpha,
        (color[2] * alpha + base[2] * base_alpha * (1.0 - alpha)) / output_alpha,
        output_alpha,
    )


def sample_icon(x: float, y: float) -> tuple[float, float, float, float]:
    # Coordinates are normalized to a 1024 × 1024 design canvas.
    cx = x - 512.0
    cy = y - 512.0
    pixel: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    shadow_distance = rounded_box_distance(cx, cy + 18.0, 420.0, 124.0)
    if shadow_distance < 100.0:
        shadow_alpha = 0.34 * math.exp(-max(shadow_distance, 0.0) / 36.0)
        if shadow_distance <= 0.0:
            shadow_alpha = 0.34
        pixel = over(pixel, (0.0, 0.0, 0.0), shadow_alpha)

    box_distance = rounded_box_distance(cx, cy, 420.0, 124.0)
    box_alpha = 1.0 - smoothstep(-1.2, 1.2, box_distance)
    if box_alpha > 0.0:
        vertical = clamp((y - 92.0) / 840.0)
        radial = clamp(1.0 - math.hypot(cx * 0.82, cy * 1.08) / 650.0)
        background = (
            20.0 + 16.0 * (1.0 - vertical) + 5.0 * radial,
            22.0 + 17.0 * (1.0 - vertical) + 7.0 * radial,
            32.0 + 21.0 * (1.0 - vertical) + 10.0 * radial,
        )
        pixel = over(pixel, background, box_alpha)

        border_distance = abs(box_distance + 9.0)
        border_alpha = (
            (1.0 - smoothstep(1.0, 4.0, border_distance))
            * 0.58
            * box_alpha
        )
        border_mix = clamp((x + y) / 2048.0)
        border = (
            63.0 + 58.0 * border_mix,
            205.0 - 100.0 * border_mix,
            188.0 + 45.0 * border_mix,
        )
        pixel = over(pixel, border, border_alpha)

    if box_alpha <= 0.0:
        return pixel

    ring_radius = math.hypot(cx, cy)
    glow = math.exp(-((ring_radius - 272.0) / 72.0) ** 2) * 0.23
    pixel = over(pixel, (42.0, 236.0, 122.0), glow * box_alpha)

    ring_distance = abs(ring_radius - 272.0)
    ring_alpha = 1.0 - smoothstep(38.0, 42.0, ring_distance)
    ring_highlight = clamp((0.35 - cy / 1024.0) + 0.45)
    ring_color = (
        38.0 + 18.0 * ring_highlight,
        211.0 + 29.0 * ring_highlight,
        104.0 + 22.0 * ring_highlight,
    )
    pixel = over(pixel, ring_color, ring_alpha * box_alpha)

    inner_ring_distance = abs(ring_radius - 220.0)
    inner_ring_alpha = (
        1.0 - smoothstep(1.5, 5.0, inner_ring_distance)
    ) * 0.24
    pixel = over(pixel, (118.0, 104.0, 226.0), inner_ring_alpha * box_alpha)

    stem = segment_distance(cx, cy, 0.0, -128.0, 0.0, 142.0)
    shoulder = segment_distance(cx, cy, -86.0, -66.0, 0.0, -136.0)
    digit_distance = min(stem, shoulder)
    digit_alpha = 1.0 - smoothstep(35.0, 40.0, digit_distance)
    pixel = over(pixel, (53.0, 229.0, 116.0), digit_alpha * box_alpha)

    highlight = math.exp(-(((cx + 112.0) / 300.0) ** 2 + ((cy + 230.0) / 130.0) ** 2))
    pixel = over(pixel, (255.0, 255.0, 255.0), highlight * 0.07 * box_alpha)
    return pixel


def render(size: int) -> bytes:
    supersampling = 4 if size <= 256 else 1
    scale = 1024.0 / size
    output = bytearray(size * size * 4)
    index = 0
    samples = supersampling * supersampling

    for y in range(size):
        for x in range(size):
            accumulated = [0.0, 0.0, 0.0, 0.0]
            for sample_y in range(supersampling):
                for sample_x in range(supersampling):
                    design_x = (
                        x + (sample_x + 0.5) / supersampling
                    ) * scale
                    design_y = (
                        y + (sample_y + 0.5) / supersampling
                    ) * scale
                    pixel = sample_icon(design_x, design_y)
                    for channel in range(4):
                        accumulated[channel] += pixel[channel]
            for channel in range(4):
                value = accumulated[channel] / samples
                output[index] = round(clamp(value, 0.0, 255.0 if channel < 3 else 1.0) * (1.0 if channel < 3 else 255.0))
                index += 1
    return bytes(output)


def png_chunk(name: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + name
        + payload
        + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
    )


def encode_png(size: int, rgba: bytes) -> bytes:
    scanlines = bytearray()
    stride = size * 4
    for row in range(size):
        scanlines.append(0)
        start = row * stride
        scanlines.extend(rgba[start : start + stride])
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(bytes(scanlines), 9))
        + png_chunk(b"IEND", b"")
    )


def encode_ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = bytearray()
    payload = bytearray()
    for size, png in images:
        encoded_size = 0 if size == 256 else size
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                encoded_size,
                encoded_size,
                0,
                0,
                1,
                32,
                len(png),
                offset,
            )
        )
        payload.extend(png)
        offset += len(png)
    return header + bytes(entries) + bytes(payload)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    encoded: dict[int, bytes] = {}

    for size in PNG_SIZES:
        rgba = render(size)
        png = encode_png(size, rgba)
        encoded[size] = png
        (OUTPUT / f"Kairos-{size}.png").write_bytes(png)

    ico = encode_ico([(size, encoded[size]) for size in ICO_SIZES])
    (OUTPUT / "Kairos.ico").write_bytes(ico)
    print(f"generated application icons in {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
