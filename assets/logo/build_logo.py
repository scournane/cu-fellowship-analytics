#!/usr/bin/env python3
"""Reproducible builder for the CU Fellowship Analytics Slack bot icon ("Ding" — the check-in bell, with a face).

    pip install cairosvg pillow
    python assets/logo/build_logo.py

Everything is authored on a 512x512 grid and rendered at 1024/512/36 from the
same SVG, so the master PNG and the Slack icon are pixel-consistent.
"""
import math, os, sys
import cairosvg
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = os.path.join(HERE, "checks")
CONCEPTS = os.path.join(HERE, "concepts")

# ---------------------------------------------------------------- palette
P = dict(
    field="#12C2A4",   # signal teal      (icon background)
    white="#FFFFFF",   # bell white       (arcs, button, base plate)
    wsh="#D3E8E2",     # white shade      (underside of button / plate)
    sun="#FFC629",     # sunny brass      (bell dome)
    sunsh="#EF9F1E",   # brass shade      (dome crescent)
    deep="#0B7A66",    # deep teal        (ground shadow, eyes)
    blush="#FFA35A",   # cheek blush      (two soft circles on the dome)
)

# ---------------------------------------------------------------- geometry helpers
def pol(cx, cy, r, a):
    a = math.radians(a)
    return cx + r * math.cos(a), cy - r * math.sin(a)

def arc(cx, cy, r, a0, a1):
    """Open arc path. Angles in degrees, 0 = east, counter-clockwise positive."""
    x0, y0 = pol(cx, cy, r, a0)
    x1, y1 = pol(cx, cy, r, a1)
    large = 1 if abs(a1 - a0) > 180 else 0
    sweep = 0 if a1 > a0 else 1
    return f"M {x0:.2f} {y0:.2f} A {r} {r} 0 {large} {sweep} {x1:.2f} {y1:.2f}"

def svg(body, bg, size=512):
    back = f'<rect width="512" height="512" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 512 512">{back}{body}</svg>')

def render(svg_text, path, px, keep_svg=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if keep_svg:
        with open(os.path.splitext(path)[0] + ".svg", "w") as f:
            f.write(svg_text)
    cairosvg.svg2png(bytestring=svg_text.encode(), write_to=path, output_width=px, output_height=px)
    return path

# ---------------------------------------------------------------- the mark
# semi-ellipse dome with straight sides carried below the base plate, so the
# shade crescent never forms a visible band at the dome's foot
DOME = "M 106 360 C 100 250, 170 184, 256 184 C 342 184, 412 250, 406 360 L 406 380 L 106 380 Z"

def ding(p=P, face=True, bg=None, mono=None, shadow=True, blush=True):
    """The bell mark.  mono=<hex> renders a single-colour silhouette (eyes are
    knocked out in the background colour so the face survives in one colour)."""
    if mono:
        p = dict(p, white=mono, wsh=mono, sun=mono, sunsh=mono, deep=mono)
    eyes = ""
    if face:
        eye_col = (bg or p["field"]) if mono else p["deep"]
        if blush and not mono:
            eyes += (f'<circle cx="174" cy="318" r="20" fill="{p["blush"]}"/>'
                     f'<circle cx="338" cy="318" r="20" fill="{p["blush"]}"/>')
        eyes += "".join(f'<path d="{arc(x,280,30,20,160)}" stroke="{eye_col}" stroke-width="34" '
                        f'fill="none" stroke-linecap="round"/>' for x in (204, 308))
    ground = f'<ellipse cx="256" cy="404" rx="160" ry="18" fill="{p["deep"]}"/>' if shadow else ""
    body = f'''
<g transform="translate(256,256) scale(1.05) translate(-256,-250)">
  {ground}
  <!-- sound arcs: two right, one left (asymmetry = motion) -->
  <g fill="none" stroke="{p['white']}" stroke-width="34" stroke-linecap="round">
    <path d="{arc(256,182,86,8,64)}"/>
    <path d="{arc(256,182,126,14,58)}"/>
    <path d="{arc(256,182,86,120,166)}"/>
  </g>
  <!-- knob (shade pill first, white pill 12px above it) -->
  <rect x="210" y="162" width="92" height="50" rx="25" fill="{p['wsh']}"/>
  <rect x="210" y="150" width="92" height="50" rx="25" fill="{p['white']}"/>
  <!-- dome: shade colour, then the bright colour shifted up-left and clipped ->
       leaves a crescent of shade along the lower-right -->
  <path d="{DOME}" fill="{p['sunsh']}"/>
  <clipPath id="dome"><path d="{DOME}"/></clipPath>
  <path d="{DOME}" fill="{p['sun']}" clip-path="url(#dome)" transform="translate(-22,-8)"/>
  {eyes}
  <!-- base plate -->
  <rect x="86" y="362" width="340" height="42" rx="21" fill="{p['wsh']}"/>
  <rect x="86" y="350" width="340" height="42" rx="21" fill="{p['white']}"/>
</g>'''
    return svg(body, bg)

# ---------------------------------------------------------------- other concepts (for the record)
def hand(p=P):
    palm = "M 172 250 C 172 222, 192 210, 216 210 L 296 210 C 320 210, 340 222, 340 250 L 340 362 C 340 390, 318 406, 290 406 L 222 406 C 194 406, 172 390, 172 362 Z"
    return svg(f'''
<g transform="translate(-34,4) rotate(12 256 400)">
  <g fill="none" stroke="{p['sun']}" stroke-width="32" stroke-linecap="round">
    <path d="{arc(300,250,150,-24,30)}"/><path d="{arc(300,250,192,-16,22)}"/></g>
  <g fill="{p['white']}">
    <rect x="176" y="102" width="52" height="180" rx="26"/>
    <rect x="232" y="78"  width="52" height="200" rx="26"/>
    <rect x="288" y="94"  width="52" height="190" rx="26"/>
    <rect x="112" y="238" width="54" height="126" rx="27" transform="rotate(-42 139 300)"/>
    <path d="{palm}"/></g>
  <clipPath id="pm"><path d="{palm}"/></clipPath>
  <rect x="290" y="300" width="60" height="120" fill="{p['wsh']}" clip-path="url(#pm)"/>
  <rect x="170" y="408" width="172" height="52" rx="24" fill="{p['sunsh']}"/>
  <rect x="170" y="398" width="172" height="52" rx="24" fill="{p['sun']}"/>
</g>''', p["field"])

def flag(p=P):
    hill = "M 30 560 L 30 452 C 90 352, 190 322, 256 328 C 340 336, 430 376, 482 452 L 482 560 Z"
    pennant = "M 210 112 C 268 78, 330 150, 414 108 C 420 148, 418 184, 424 220 C 336 258, 272 186, 214 214 Z"
    return svg(f'''
<clipPath id="hl"><path d="{hill}"/></clipPath>
<clipPath id="pn"><path d="{pennant}"/></clipPath>
<path d="{hill}" fill="{p['white']}"/>
<path d="{hill}" fill="{p['wsh']}" clip-path="url(#hl)" transform="translate(0,40)"/>
<path d="{pennant}" fill="{p['sunsh']}"/>
<path d="{pennant}" fill="{p['sun']}" clip-path="url(#pn)" transform="translate(-10,-12)"/>
<line x1="194" y1="400" x2="204" y2="106" stroke="{p['white']}" stroke-width="34" stroke-linecap="round"/>''', p["field"])

# ---------------------------------------------------------------- check sheets
def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return m

def slack_tile(icon, size):
    """Slack masks app icons to ~22% corner radius; simulate it."""
    im = icon.resize((size, size), Image.LANCZOS).convert("RGBA")
    im.putalpha(rounded_mask(size, int(size * 0.22)))
    return im

def modes_sheet(icon_png, out):
    icon = Image.open(icon_png).convert("RGBA")
    W, H = 1040, 560
    sheet = Image.new("RGB", (W, H), (238, 238, 234))
    surfaces = [("Light mode  #FFFFFF / sidebar #F8F8F8", (255, 255, 255), (248, 248, 248), (29, 28, 29)),
                ("Dark mode  #1A1D21 / sidebar #19171D", (26, 29, 33), (25, 23, 29), (209, 210, 211))]
    for col, (label, bg, side, fg) in enumerate(surfaces):
        x0 = 20 + col * 510
        panel = Image.new("RGB", (490, 520), bg)
        d = ImageDraw.Draw(panel)
        d.rectangle((0, 0, 200, 520), fill=side)
        d.text((14, 14), label, fill=fg)
        # sidebar rows: 20px + name, one highlighted
        for i, (name, hi) in enumerate([("general", False), ("fellowship-ops", False), ("CU Fellowship", True), ("check-ins", False)]):
            y = 60 + i * 34
            if hi:
                d.rectangle((6, y - 6, 194, y + 26), fill=(18, 100, 163) if col == 0 else (17, 100, 163))
            t = slack_tile(icon, 20)
            panel.paste(t, (16, y), t)
            d.text((44, y + 3), name, fill=(255, 255, 255) if hi else fg)
        # app directory tiles
        for j, s in enumerate((36, 48, 64, 96)):
            t = slack_tile(icon, s)
            panel.paste(t, (230 + j * 0 + (0 if j < 2 else 130), 60 + (0 if j % 2 == 0 else 120) + (0 if j < 2 else 0)), t)
        t = slack_tile(icon, 192); panel.paste(t, (240, 300), t)
        d.text((240, 500), "36 / 48 / 64 / 96 / 192 px", fill=fg)
        sheet.paste(panel, (x0, 20))
    sheet.save(out)

def tiny_sheet(icon_png, out):
    icon = Image.open(icon_png).convert("RGB")
    sizes = (16, 20, 24, 32, 36, 48, 64)
    W = sum(sizes) + 24 * (len(sizes) + 1)
    sheet = Image.new("RGB", (W, 120), (248, 248, 248))
    d = ImageDraw.Draw(sheet)
    x = 24
    for s in sizes:
        sheet.paste(icon.resize((s, s), Image.LANCZOS), (x, 24))
        d.text((x, 96), f"{s}", fill=(60, 60, 60))
        x += s + 24
    big = sheet.resize((W * 3, 360), Image.NEAREST)
    big.save(out)

def sheet(paths, out, cell=512, cols=2, pad=24):
    rows = (len(paths) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell + pad * (cols + 1), rows * cell + pad * (rows + 1)), (238, 238, 234))
    for i, p in enumerate(paths):
        canvas.paste(Image.open(p).convert("RGB").resize((cell, cell), Image.LANCZOS),
                     (pad + (i % cols) * (cell + pad), pad + (i // cols) * (cell + pad)))
    canvas.save(out)

# ---------------------------------------------------------------- build
def main():
    # final icon
    master = ding(bg=P["field"])
    render(master, os.path.join(HERE, "cufa-icon-master-1024.png"), 1024)
    render(master, os.path.join(HERE, "cufa-icon-512.png"), 512, keep_svg=False)
    with open(os.path.join(HERE, "cufa-icon.svg"), "w") as f:
        f.write(master)
    # transparent marks: one tuned for dark surfaces (white), one for light (deep teal)
    render(ding(bg=None, shadow=False), os.path.join(HERE, "cufa-mark-on-dark.png"), 1024, keep_svg=True)
    p_light = dict(P, white=P["deep"], wsh="#075C4C")
    render(ding(p=p_light, bg=None, shadow=False), os.path.join(HERE, "cufa-mark-on-light.png"), 1024, keep_svg=True)
    # alt: the same bell without the face (see README)
    render(ding(face=False, bg=P["field"]), os.path.join(HERE, "cufa-icon-plain-512.png"), 512)

    # concepts
    cs = [("1-ding-bell", ding(face=False, bg=P["field"])), ("2-hey-hand", hand()),
          ("3-here-flag", flag()), ("4-ding-face", ding(face=True, bg=P["field"]))]
    cpaths = [render(s, os.path.join(CONCEPTS, f"concept-{n}-512.png"), 512) for n, s in cs]
    sheet(cpaths, os.path.join(CONCEPTS, "concepts-sheet.png"))

    # checks
    icon512 = os.path.join(HERE, "cufa-icon-512.png")
    tiny_sheet(icon512, os.path.join(CHECKS, "tiny-16-to-64.png"))
    modes_sheet(icon512, os.path.join(CHECKS, "dark-light-modes.png"))
    mono = [render(ding(mono="#FFFFFF", bg="#0B7A66"), os.path.join(CHECKS, "mono-white-on-deep.png"), 512),
            render(ding(mono="#1D1C1D", bg="#FFFFFF"), os.path.join(CHECKS, "mono-ink-on-white.png"), 512),
            render(ding(bg=P["field"]), os.path.join(CHECKS, "_tmp_color.png"), 512)]
    Image.open(mono[2]).convert("L").convert("RGB").save(os.path.join(CHECKS, "grayscale.png"))
    os.remove(mono[2])
    sheet([mono[0], mono[1], os.path.join(CHECKS, "grayscale.png"), icon512],
          os.path.join(CHECKS, "mono-and-grayscale-sheet.png"), cell=360)
    print("built", HERE)

if __name__ == "__main__":
    main()
