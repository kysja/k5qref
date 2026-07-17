"""Generate the k5qref tray and Windows executable icons."""

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
PRIMARY = "#7378DC"
TRANSPARENT = (0, 0, 0, 0)


def cubic_bezier(p0, p1, p2, p3, steps: int = 18):
    """Return evenly sampled points along a cubic Bezier curve."""
    points = []
    for step in range(steps + 1):
        t = step / steps
        u = 1 - t
        points.append(
            (
                u**3 * p0[0]
                + 3 * u**2 * t * p1[0]
                + 3 * u * t**2 * p2[0]
                + t**3 * p3[0],
                u**3 * p0[1]
                + 3 * u**2 * t * p1[1]
                + 3 * u * t**2 * p2[1]
                + t**3 * p3[1],
            )
        )
    return points


def make_icon(size: int) -> Image.Image:
    """Draw memory concept F with geometry legible at small sizes."""
    scale = 8
    canvas_size = size * scale
    image = Image.new("RGBA", (canvas_size, canvas_size), TRANSPARENT)
    draw = ImageDraw.Draw(image)

    def px(value: float) -> int:
        return round(value * canvas_size)

    # Standalone bookmark silhouette. The broad body and deep lower notch
    # remain recognizable in the 16px tray rendering.
    draw.polygon(
        [
            (px(0.16), px(0.20)),
            (px(0.84), px(0.20)),
            (px(0.84), px(0.90)),
            (px(0.50), px(0.70)),
            (px(0.16), px(0.90)),
        ],
        fill=PRIMARY,
    )
    draw.rounded_rectangle(
        (px(0.16), px(0.08), px(0.84), px(0.38)),
        radius=px(0.115),
        fill=PRIMARY,
    )

    # Transparent memory/brain outline cut into the bookmark. The open,
    # asymmetric contour reads more clearly than anatomical detail at 16px.
    curves = [
        ((0.39, 0.61), (0.39, 0.57), (0.39, 0.54), (0.39, 0.51)),
        ((0.39, 0.51), (0.34, 0.50), (0.31, 0.46), (0.31, 0.41)),
        ((0.31, 0.41), (0.31, 0.31), (0.39, 0.24), (0.50, 0.24)),
        ((0.50, 0.24), (0.60, 0.24), (0.67, 0.30), (0.67, 0.39)),
        ((0.67, 0.39), (0.72, 0.40), (0.72, 0.46), (0.68, 0.49)),
        ((0.68, 0.49), (0.65, 0.52), (0.61, 0.51), (0.61, 0.56)),
        ((0.61, 0.56), (0.61, 0.58), (0.61, 0.60), (0.61, 0.61)),
    ]
    contour = []
    for curve in curves:
        segment = cubic_bezier(*curve)
        contour.extend(segment if not contour else segment[1:])
    contour = [(px(x), px(y)) for x, y in contour]

    cut_width = px(0.063)
    draw.line(contour, fill=TRANSPARENT, width=cut_width, joint="curve")
    radius = cut_width // 2
    for x, y in (contour[0], contour[-1]):
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=TRANSPARENT,
        )

    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    make_icon(512).save(ASSETS / "k5qref.png", optimize=True)
    make_icon(256).save(
        ASSETS / "k5qref.ico",
        format="ICO",
        sizes=[
            (16, 16),
            (20, 20),
            (24, 24),
            (32, 32),
            (40, 40),
            (48, 48),
            (64, 64),
            (128, 128),
            (256, 256),
        ],
    )


if __name__ == "__main__":
    main()
