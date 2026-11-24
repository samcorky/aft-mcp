# /// script
# requires-python = ">=3.14"
# dependencies = ["pillow>=13.0.0"]
# ///
"""Make aft-mcp's logo: AFT's logo with an "MCP" banner across it.

AFT's logo (``assets/source/AFT_logo.png``, from sjefferson99/aft, MIT licensed) is a
bitmap, so the logo is built from it here rather than drawn by hand, and can be made
again with::

    uv run --script scripts/make_logo.py

It writes, next to the source:

- ``assets/logo.png`` (512 px), for the README;
- ``assets/logo-32.png`` (32 px), to check the banner still reads that small;
- ``assets/social-preview.png`` (1280x640), for GitHub's social preview;
- ``src/aft_mcp/icon.png`` (64 px), the icon the MCP server advertises.

The banner text uses Pillow's own scalable font, so the result doesn't depend on the
fonts a machine has installed.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "source" / "AFT_logo.png"
ASSETS = ROOT / "assets"
PACKAGE_ICON = ROOT / "src" / "aft_mcp" / "icon.png"

NAVY = (22, 50, 79, 255)  # the banner: dark enough for white text, and on light pages
WHITE = (255, 255, 255, 255)
MINT = (106, 211, 160, 255)  # AFT's green, for the social preview's accent
SIZE = 1024  # drawn large, then scaled down, for smooth edges


def mark() -> Image.Image:
    """AFT's logo in its green, with the white background made transparent.

    The source is a flat mint mark on an opaque, slightly noisy white. Each pixel's
    opacity comes from how far its red channel is from white (255) towards the mint's
    (108), which keeps the edges anti-aliased; the gaps between the cards become
    transparent too, so the logo works on dark pages.
    """
    source = Image.open(SOURCE).convert("RGB")
    red = source.getchannel("R")
    alpha = red.point(
        lambda r: max(0, min(255, round((250 - r) * 255 / (250 - MINT[0]))))
    )
    out = Image.new("RGBA", source.size, MINT)
    out.putalpha(alpha)
    return out


def logo(size: int) -> Image.Image:
    """The square logo at ``size`` px, on a transparent background."""
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    source = mark()
    # The A fills the top of the square; the banner sits over its legs.
    scale = (SIZE * 0.86) / max(source.size)
    scaled = source.resize(
        (round(source.width * scale), round(source.height * scale)),
        Image.Resampling.LANCZOS,
    )
    canvas.alpha_composite(scaled, ((SIZE - scaled.width) // 2, round(SIZE * 0.04)))

    draw = ImageDraw.Draw(canvas)
    top, bottom = round(SIZE * 0.62), round(SIZE * 0.90)
    draw.rounded_rectangle(
        (round(SIZE * 0.06), top, round(SIZE * 0.94), bottom),
        radius=round(SIZE * 0.06),
        fill=NAVY,
        outline=WHITE,
        width=round(SIZE * 0.012),
    )
    font = ImageFont.load_default(size=round((bottom - top) * 0.78))
    text = "MCP"
    stroke = round(SIZE * 0.012)  # thickens the default font into a bold
    left, upper, right, lower = draw.textbbox(
        (0, 0), text, font=font, stroke_width=stroke
    )
    x = (SIZE - (right - left)) / 2 - left
    y = top + ((bottom - top) - (lower - upper)) / 2 - upper
    draw.text(
        (x, y), text, font=font, fill=WHITE, stroke_width=stroke, stroke_fill=WHITE
    )
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def social_preview() -> Image.Image:
    """GitHub's 1280x640 card: the logo and the project's name and purpose."""
    card = Image.new("RGBA", (1280, 640), NAVY)
    card.alpha_composite(logo(440), (90, 100))
    draw = ImageDraw.Draw(card)
    draw.rectangle((0, 610, 1280, 640), fill=MINT)
    title = ImageFont.load_default(size=112)
    body = ImageFont.load_default(size=40)
    draw.text(
        (600, 190), "aft-mcp", font=title, fill=WHITE, stroke_width=3, stroke_fill=WHITE
    )
    lines = [
        "An MCP server that gives AI",
        "agents safe, scoped access",
        "to your AFT boards.",
    ]
    for number, line in enumerate(lines):
        draw.text((604, 345 + number * 56), line, font=body, fill=WHITE)
    return card.convert("RGB")


def main() -> None:
    """Write every image."""
    logo(512).save(ASSETS / "logo.png", optimize=True)
    logo(32).save(ASSETS / "logo-32.png", optimize=True)
    logo(64).save(PACKAGE_ICON, optimize=True)
    social_preview().save(ASSETS / "social-preview.png", optimize=True)
    for path in (
        ASSETS / "logo.png",
        ASSETS / "logo-32.png",
        PACKAGE_ICON,
        ASSETS / "social-preview.png",
    ):
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
