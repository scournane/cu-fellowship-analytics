# CU Fellowship Analytics — Slack bot icon

**Mark: "Ding" — the check-in bell.**

A front-desk bell is the one everyday object that already means *"I'm here"*
and *"someone needs a hand"* at the same time, from the staff side of the
counter. Ringing it is a signal, not surveillance; answering it is care. The
sound arcs give it motion, the brass dome gives it warmth, and the whole thing
is a chunky object caught mid-gesture rather than a featureless figure or an
abstract glyph.

| File | Use |
| --- | --- |
| `cufa-icon-512.png` | **Slack app icon.** Upload as-is. Square corners — Slack applies its own mask. |
| `cufa-icon-master-1024.png` | Master raster (App Directory, docs, decks). |
| `cufa-icon.svg` | Master vector, 512-unit grid. Source of truth. |
| `cufa-mark-on-dark.png/.svg` | Transparent mark, white + brass. For dark surfaces (Slack dark sidebar, dark slides). |
| `cufa-mark-on-light.png/.svg` | Transparent mark, deep-teal + brass. For light surfaces (docs, white slides). |
| `cufa-icon-face-512.png` | Bonus: the bell with closed happy eyes. See *Face variant* below. |
| `concepts/` | The four concepts at 512 plus a contact sheet. |
| `checks/` | Tiny-size, dark/light mode, monochrome and grayscale checks. |
| `build_logo.py` | Regenerates everything. `pip install cairosvg pillow && python assets/logo/build_logo.py` |

---

## Concepts

All four were rendered on the same system palette so the comparison is about
the idea, not the colour. `concepts/concepts-sheet.png` shows them together.

### 1. Ding — check-in desk bell  ✅ chosen
*A desk bell is the universal "I'm here / can someone help" object, and its ring is a signal you answer, not a thing you watch.*

- **36 px:** brass dome + white base + two white arcs read as "bell ringing". The knob softens to a nub at 24 px and below, so the arcs carry the "signal" read; at 16–20 px it simplifies to "gold bell on a plate", which is still distinct against blue/purple bot icons.
- **Palette:** signal teal `#12C2A4` · white `#FFFFFF` · sunny brass `#FFC629` · deep teal `#0B7A66` (+ shade tones `#EF9F1E`, `#D3E8E2`).

### 2. Hey — waving hand
*A chunky mitten hand mid-wave is the most human "present!" gesture and doubles as "I need a hand".*

- **36 px:** reads as a hand, but the finger gaps close up and it drifts toward the 👋 emoji / a cartoon glove; less ownable than the bell.
- **Palette:** same system; alt field coral `#FF6B4A` with white hand and teal cuff if you want it warmer.

### 3. Here — planted waving flag
*A pennant planted on a hill says "checked in, milestone reached" and the flutter gives it momentum.*

- **36 px:** reads as a golf hole marker, and "flag" carries a "flagged for review" connotation in ops language. Dropped.
- **Palette:** same system; alt field sky `#2F9BFF` reads more "outdoors / summit".

### 4. Ding with a face
*Same bell, two closed happy eyes — the direct Duolingo move.*

- **36 px:** the eyes become 1.5 px smudges, which is exactly the "mascot face with tiny features" trap in the brief. Charming at 128 px+. Kept as a bonus asset, not the icon.
- **Palette:** same system; eyes in deep teal `#0B7A66`.

**Why 1 over 4:** the brief's small-size read is "clear signal / check-in / people cared for". The plain bell delivers all three at 36 px with zero features that can degrade. The face adds warmth only at sizes Slack rarely shows.

---

## Final mark — construction notes

Authored on a **512 × 512** grid (1 unit = 2 px in the 1024 master). All
geometry is circles, pills, one cubic-bezier dome and three arcs — nothing
that needs a boolean or a gradient.

### Background
- Full-bleed square `rect 0 0 512 512`, fill **`#12C2A4`**.
- **Square corners.** Slack masks to ~22 % radius; `checks/dark-light-modes.png` simulates that.

### Mark group
Wrapped in `translate(256,256) scale(1.05) translate(-256,-250)`: the mark is
built slightly small, scaled 5 % about (256, 250), then nudged 6 units down so
the arcs don't crowd the top edge.

Bounding box after transform ≈ **x 75–434, y 54–437** (shadow included) → 70 % wide, 75 % tall; 71 % tall without the shadow.
Minimum padding 54 units top (10.5 %), 75 units each side (15 %).

### Foreground shapes, back to front
| # | Shape | Geometry (512 grid) | Fill / stroke |
| --- | --- | --- | --- |
| 1 | Ground shadow | ellipse c(256, 404) rx 160 ry 18 | `#0B7A66` |
| 2 | Sound arcs (×3) | centre (256, 182); r 86 @ 8°→64°, r 126 @ 14°→58°, r 86 @ 120°→166° (0° = east, CCW) | stroke `#FFFFFF`, **34**, round caps, no fill |
| 3 | Knob shade | pill 92 × 50, rx 25 at (210, 162) | `#D3E8E2` |
| 4 | Knob | pill 92 × 50, rx 25 at (210, 150) | `#FFFFFF` |
| 5 | Dome, shade layer | `M 106 360 C 100 250,170 184,256 184 C 342 184,412 250,406 360 L 406 380 L 106 380 Z` | `#EF9F1E` |
| 6 | Dome, lit layer | same path, clipped to itself, `translate(-22,-8)` — leaves a shade crescent down the right side | `#FFC629` |
| 7 | Plate shade | rect 340 × 42, rx 21 at (86, 362) | `#D3E8E2` |
| 8 | Base plate | rect 340 × 42, rx 21 at (86, 350) | `#FFFFFF` |

Two arcs on the right and one on the left: the asymmetry is what makes it
read as a *ring* rather than a static symbol.

### Stroke widths and minimum features
- Arcs: 34 units (≈ 36 after scale) → **2.5 px at 36 px**, 1.4 px at 20 px.
- Knob: 50 units tall → 3.5 px at 36 px.
- Plate: 42 units tall → 3 px at 36 px.
- Nothing thinner than 34 units anywhere.

### Colour
Four main colours plus one derived shade each for the two lit surfaces.

| Role | Hex | Grayscale luma |
| --- | --- | --- |
| Signal teal (field) | `#12C2A4` | 139 |
| White (arcs, knob, plate) | `#FFFFFF` | 255 |
| White shade | `#D3E8E2` | 225 |
| Sunny brass (dome) | `#FFC629` | 197 |
| Brass shade | `#EF9F1E` | 168 |
| Deep teal (shadow) | `#0B7A66` | 86 |

Lumas are spread 86 / 139 / 197 / 255 across the four main colours, which is
why `checks/grayscale.png` still reads. Nothing in here is Slack's aubergine
(`#4A154B`) or its four hash colours (`#36C5F0 #2EB67D #ECB22E #E01E5A`), and
the teal is well away from Duolingo green (`#58CC02`).

### Transparent variants
The mark is mostly white, so a single transparent PNG can't work on both
surfaces. Two are provided, both without the ground shadow:
- **on-dark:** white + brass, as in the icon.
- **on-light:** the white parts swapped to deep teal `#0B7A66` (shade `#075C4C`), brass unchanged.

---

## Checks

- `checks/tiny-16-to-64.png` — 16 / 20 / 24 / 32 / 36 / 48 / 64 px, Lanczos-downsampled, shown at 3×.
- `checks/dark-light-modes.png` — Slack-style sidebar rows at 20 px (light `#F8F8F8`, dark `#19171D`) and 36–192 px tiles with the ~22 % mask applied.
- `checks/mono-white-on-deep.png`, `checks/mono-ink-on-white.png` — single-colour silhouette. The bell + arcs outline holds without any interior shading.
- `checks/grayscale.png` — desaturated icon.

## Face variant

`cufa-icon-face-512.png` is the same construction plus two eye arcs
(centres (212, 290) and (300, 290), r 26, 20°→160°, stroke 28, `#0B7A66`).
Use it for larger-format moments — a welcome message header, a custom emoji
(`:ding:`), a bot "reaction" — not as the app icon.
