"""Generate themed app icons from assets/icon_template.svg.

For each theme it renders a colored PNG (used as the menu-bar icon) into
``assets/menubar/<theme>.png``, and builds ``assets/icon.icns`` (app bundle
icon) from the default theme.

SVG -> PNG uses ``rsvg-convert`` (librsvg) if available, otherwise falls back
to macOS ``qlmanage``. PNG -> icns uses ``iconutil`` + ``sips`` (macOS builtin).

@author SteveZou
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
TEMPLATE = ASSETS / "icon_template.svg"
CHECKING_TEMPLATE = ASSETS / "icon_checking_template.svg"
MENUBAR_DIR = ASSETS / "menubar"

# theme -> (C1 top, C3 middle, C2 bottom, GLOW) for the bolt's 3-stop gradient.
# Solid themes use close shades so the bolt looks single-hued; Rainbow spans the
# spectrum so it reads as multicolor. Keep in sync with THEMES in
# src/notifier_app.py.
THEME_COLORS = {
    "Orange": ("#ffb347", "#ff8a3c", "#ff5e3a", "#ff7a45"),
    "Green": ("#5ff0b0", "#34e0a1", "#0ea5a5", "#22d3aa"),
    "Purple": ("#d6a8ff", "#c084fc", "#7c3aed", "#a855f7"),
    "Rainbow": ("#ff5e5e", "#ffd166", "#3b82f6", "#a855f7"),
    "Yellow": ("#fff08a", "#fde047", "#f59e0b", "#fbbf24"),
}
DEFAULT_THEME = "Orange"

# menu-bar icons render small; 44px looks crisp on retina.
MENUBAR_PX = 44


def _render_svg_to_png(svg_path: Path, png_path: Path, px: int) -> None:
    if shutil.which("rsvg-convert"):
        subprocess.run(
            ["rsvg-convert", "-w", str(px), "-h", str(px),
             str(svg_path), "-o", str(png_path)],
            check=True, capture_output=True,
        )
        return
    # Fallback: qlmanage renders a thumbnail (name is <svg>.png in outdir).
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(
            ["qlmanage", "-t", "-s", str(px), "-o", td, str(svg_path)],
            check=True, capture_output=True,
        )
        produced = next(Path(td).glob("*.png"), None)
        if not produced:
            raise RuntimeError("qlmanage produced no PNG")
        shutil.copy(produced, png_path)


def _themed_svg(theme: str, tmp_dir: Path, template: Path = TEMPLATE,
                suffix: str = "") -> Path:
    c1, c3, c2, glow = THEME_COLORS[theme]
    text = template.read_text(encoding="utf-8")
    text = (text.replace("{C1}", c1).replace("{C3}", c3)
                .replace("{C2}", c2).replace("{GLOW}", glow))
    out = tmp_dir / f"{theme}{suffix}.svg"
    out.write_text(text, encoding="utf-8")
    return out


def _build_icns(svg_path: Path, out_icns: Path, tmp_dir: Path) -> None:
    base = tmp_dir / "base_1024.png"
    _render_svg_to_png(svg_path, base, 1024)
    iconset = tmp_dir / "icon.iconset"
    iconset.mkdir()
    for s in (16, 32, 64, 128, 256, 512, 1024):
        for scale, suffix in ((1, ""), (2, "@2x")):
            px = s * scale
            if px > 1024:
                continue
            name = f"icon_{s}x{s}{suffix}.png"
            subprocess.run(
                ["sips", "-z", str(px), str(px), str(base),
                 "--out", str(iconset / name)],
                check=True, capture_output=True,
            )
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(out_icns)],
        check=True,
    )


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    MENUBAR_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for theme in THEME_COLORS:
            svg = _themed_svg(theme, tmp)
            png = MENUBAR_DIR / f"{theme}.png"
            _render_svg_to_png(svg, png, MENUBAR_PX)
            print(f"menu-bar icon: {png}")
            # "checking" (busy) variant, same theme colors, spinner arc.
            if CHECKING_TEMPLATE.exists():
                csvg = _themed_svg(theme, tmp, CHECKING_TEMPLATE, "-checking")
                cpng = MENUBAR_DIR / f"{theme}-checking.png"
                _render_svg_to_png(csvg, cpng, MENUBAR_PX)
                print(f"menu-bar icon: {cpng}")
        # app icon from default theme
        default_svg = _themed_svg(DEFAULT_THEME, tmp)
        _build_icns(default_svg, ASSETS / "icon.icns", tmp)
        print(f"app icon: {ASSETS / 'icon.icns'}")


if __name__ == "__main__":
    main()
