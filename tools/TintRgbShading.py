"""Reference for the tint shaders' exact-RGB shading (sw_shader/fs_plt_tinter*.shd and the
other tint shaders). TestPaletteSwatchLookup and TestTintShaderMaterials check the shaders against it.
"""
from __future__ import annotations

# The tint shaders' exact-RGB shading constants (see custom_rgb_shades).
RGB_LUMINANCE = (0.2126, 0.7152, 0.0722)
# Orthonormal basis of the chroma plane (orthogonal to RGB_LUMINANCE), as the shaders write it.
RGB_CHROMA_U = (0.675495915, -0.127481049, -0.726260181)
RGB_CHROMA_V = (-0.680638933, 0.271037427, -0.680638933)
# Squared midtone chroma (8 levels) at which a swatch's hue counts half.
RGB_CHROMA_CONFIDENCE = 0.000984237
# A swatch whose midtone luminance is below this lends the layer's first row's shading and coverage.
RGB_DARK_SWATCH = 2 / 255
# Colors this many levels from their swatch take none of a near-neutral swatch's leftover hue.
RGB_SWATCH_REACH = 16.0
# Reflection coverage fades from the swatch row's (within half a level) to the layer's first row's (this far).
RGB_COVERAGE_REACH = 8.0


def custom_rgb_shades(rgb, swatch_row, first_row) -> list[tuple[float, float, float, float]]:
    """Reference for the tint shaders' exact-RGB shading of one layer.

    rgb is the requested 0-255 color; swatch_row and first_row are the 256
    RGBA shades (0-1) of the color's nearest swatch and of the layer's first
    palette row. The swatch lends its contrast (row luminance over midtone
    luminance) to the color's own luminance, and its change of hue and
    saturation from shade to shade relative to the color's own chroma, so a
    neutral color stays neutral on a colored swatch's row and a color equal
    to a swatch renders exactly like that row. A swatch darker than two levels
    (the black rows, skin row 31) is replaced by the layer's first row, its
    shading and coverage: its ramp would turn a near-black dye white, and the duplicate
    black rows differ only in reflection, which a custom color cannot choose.
    Coverage (alpha) fades from the swatch row's to the first row's within
    eight levels of the swatch, so neighbouring colors do not flip between a
    shiny and a painted metal row's reflection when their nearest swatch changes.
    """
    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    def chroma(color):
        grey = dot(color, RGB_LUMINANCE)
        offset = [value - grey for value in color]
        return dot(offset, RGB_CHROMA_U), dot(offset, RGB_CHROMA_V)

    target = [channel / 255 for channel in rgb]
    if dot(swatch_row[128][:3], RGB_LUMINANCE) < RGB_DARK_SWATCH:
        swatch_row = first_row
    midpoint = swatch_row[128][:3]
    ramp_luminance = max(dot(midpoint, RGB_LUMINANCE), 1 / 255)
    color_chroma, midpoint_chroma = chroma(target), chroma(midpoint)
    midpoint_squared = dot(midpoint_chroma, midpoint_chroma)
    weight = 1 / (midpoint_squared + RGB_CHROMA_CONFIDENCE)
    confidence = midpoint_squared * weight
    relative = (dot(color_chroma, midpoint_chroma) * weight,
                (color_chroma[1] * midpoint_chroma[0] - color_chroma[0] * midpoint_chroma[1]) * weight)
    distance = dot([a - b for a, b in zip(target, midpoint)], [a - b for a, b in zip(target, midpoint)]) ** .5
    near = min(max(1 - distance * 255 / RGB_SWATCH_REACH, 0), 1)
    # Full swatch coverage within half a level, so float noise on an exact swatch cannot blend it.
    keep = min(max((RGB_COVERAGE_REACH - distance * 255) / (RGB_COVERAGE_REACH - .5), 0), 1)
    luminance = dot(target, RGB_LUMINANCE)
    shades = []
    for shade in range(256):
        scale = max(dot(swatch_row[shade][:3], RGB_LUMINANCE) / ramp_luminance, 0)
        shade_chroma = chroma(swatch_row[shade][:3])
        u = (color_chroma[0] * scale * (1 - confidence) + shade_chroma[0] * relative[0] - shade_chroma[1] * relative[1]
             + (shade_chroma[0] - midpoint_chroma[0] * scale) * (1 - confidence) * near)
        v = (color_chroma[1] * scale * (1 - confidence) + shade_chroma[0] * relative[1] + shade_chroma[1] * relative[0]
             + (shade_chroma[1] - midpoint_chroma[1] * scale) * (1 - confidence) * near)
        shades.append(tuple(min(max(luminance * scale + u * a + v * b, 0), 1)
                            for a, b in zip(RGB_CHROMA_U, RGB_CHROMA_V))
                      + (first_row[shade][3] + (swatch_row[shade][3] - first_row[shade][3]) * keep,))
    return shades
