"""Render assets/app.svg to an exact-size multi-resolution ICO (16/24/32/48/64/128/256) and a
256 px PNG for toasts. Each size is rendered from the SVG; nothing is ever upscaled.

    .venv\\Scripts\\python scripts\\make_icons.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    sys.path.insert(0, str(ROOT))
    from jamcrammer.app.icons import render_svg_file

    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    svg = ROOT / "assets" / "app.svg"
    out_dir = ROOT / "assets" / "icons"
    frames: list[Image.Image] = []
    for px in SIZES:
        image = render_svg_file(svg, px)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        frames.append(Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA"))
    ico = out_dir / "app.ico"
    frames[-1].save(ico, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    frames[-1].save(out_dir / "app.png", format="PNG")
    print(f"wrote {ico} with sizes {SIZES} and app.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
