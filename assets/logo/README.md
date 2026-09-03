# CU Fellowship Analytics — Slack bot icon

**Mark: "Ding" — the check-in bell, with a face.**

A front-desk bell is the one everyday object that already means *"I'm here"*
and *"someone needs a hand"* at the same time, from the staff side of the
counter. Ringing it is a signal, not surveillance; answering it is care. The
sound arcs give it motion, the brass dome gives it warmth, and two closed happy
eyes make it a companion rather than a fixture — it's pleased you showed up.

| File | Use |
| --- | --- |
| `cufa-icon-512.png` | **Slack app icon.** Upload as-is. Square corners — Slack applies its own mask. |
| `cufa-icon-master-1024.png` | Master raster (App Directory, docs, decks). |
| `cufa-icon.svg` | Master vector, 512-unit grid. Source of truth. |
| `cufa-mark-on-dark.png/.svg` | Transparent mark, white + brass. For dark surfaces (Slack dark sidebar, dark slides). |
| `cufa-mark-on-light.png/.svg` | Transparent mark, deep-teal + brass. For light surfaces (docs, white slides). |
| `cufa-icon-plain-512.png` | Alt: the same bell without the face. See *Plain variant* below. |
| `concepts/` | The four concepts at 512 plus a contact sheet. |
| `checks/` | Tiny-size, dark/light mode, monochrome and grayscale checks. |
| `build_logo.py` | Regenerates everything. `pip install cairosvg pillow && python assets/logo/build_logo.py` |

---

## Concepts

All four were rendered on the same system palette so the comparison is about
the idea, not the colour. `concepts/concepts-sheet.png` shows them together.

### 1. Ding — check-in desk bell
*A desk bell is the universal "I'm here / can someone help" object, and its ring is a signal you answer, not a thing you watch.*

- **36 px:** brass dome + white base + two white arcs read as "bell ringing". Safest small-size read of the set; kept as the plain alt.
- **Palette:** signal teal `#12C2A4` · white `#FFFFFF` · sunny brass `#FFC629` · deep teal `#0B7A66` (+ shade tones `#EF9F1E`, `#D3E8E2`).

### 2. Hey — waving hand
*A chunky mitten hand mid-wave is the most human "present!" gesture and doubles as "I need a hand".*

- **36 px:** reads as a hand, but the finger gaps close up and it drifts toward the 👋 emoji / a cartoon glove; less ownable than the bell.
- **Palette:** same system; alt field coral `#FF6B4A` with white hand and teal cuff if you want it warmer.

### 3. Here — planted waving flag
*A pennant planted on a hill says "checked in, milestone reached" and the flutter gives it momentum.*

- **36 px:** reads as a golf hole marker, and "flag" carries a "flagged for review" connotation in ops language. Dropped.
- **Palette:** same system; alt field sky `#2F9BFF` reads more "outdoors / summit".

### 4. Ding with a face  ✅ chosen
*Same bell, two closed happy eyes and a touch of blush — the warmth the brief's "youth-centered, cared for" read needs.*

- **36 px:** as first drawn the eyes were 1.5 px smudges. The final version bumps them to r 30 / stroke 34 (≈ 3.3 px tall at 36 px) and pulls them closer together, so they read as two dark arcs down to 32 px. At 20 px (Slack's sidebar) they merge into a single dark band across the dome — it reads as "smiling gold bell", not as individual eyes, which is fine. The blush fades to a faint warm tint below 48 px and disappears by 24 px; it never becomes clutter.
- **Palette:** same system + cheek blush `#FFA35A`.

**Why 4 over 1:** the plain bell is the safer small-size mark, but it's a fixture. The face is what makes the bot feel like a colleague on the staff side of the counter — the thing the brief calls "care, clarity, momentum". The eye sizing above is what makes that choice hold at Slack's sizes; the closed-eye shape is deliberate: no pupils, nothing that can read as watching.

---

## Final mark — construction notes

Authored on a **512 × 512** grid (1 unit = 2 px in the 1024 master). All
geometry is circles, pills, one cubic-bezier dome and five arcs — nothing
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
| 7 | Blush (×2) | circles c(174, 318) and c(338, 318), r 20 | `#FFA35A` |
| 8 | Eyes (×2) | arcs centred (204, 280) and (308, 280), r 30, 20°→160° (a ∩) | stroke `#0B7A66`, **34**, round caps, no fill |
| 9 | Plate shade | rect 340 × 42, rx 21 at (86, 362) | `#D3E8E2` |
| 10 | Base plate | rect 340 × 42, rx 21 at (86, 350) | `#FFFFFF` |

Two sound arcs on the right and one on the left: the asymmetry is what makes
it read as a *ring* rather than a static symbol. The eyes stay symmetric —
calm, content, not glancing at anything.

### Stroke widths and minimum features
- Sound arcs and eyes: 34 units (≈ 36 after scale) → **2.5 px at 36 px**, 1.4 px at 20 px.
- Eye overall height: 47 units → 3.3 px at 36 px. Gap between the eyes: 10 units.
- Knob: 50 units tall → 3.5 px at 36 px.
- Plate: 42 units tall → 3 px at 36 px.
- Blush: 40 units across → 2.8 px at 36 px, but low-contrast by design (luma 182 on 197), so it reads as warmth, never as a dot.
- Nothing structural is thinner than 34 units.

### Colour
Four main colours, one derived shade each for the two lit surfaces, and the blush.

| Role | Hex | Grayscale luma |
| --- | --- | --- |
| Signal teal (field) | `#12C2A4` | 139 |
| White (arcs, knob, plate) | `#FFFFFF` | 255 |
| White shade | `#D3E8E2` | 225 |
| Sunny brass (dome) | `#FFC629` | 197 |
| Brass shade | `#EF9F1E` | 168 |
| Cheek blush | `#FFA35A` | 182 |
| Deep teal (shadow, eyes) | `#0B7A66` | 86 |

Lumas are spread 86 / 139 / 197 / 255 across the four main colours, which is
why `checks/grayscale.png` still reads — including the eyes, at 86 on 197.
Nothing in here is Slack's aubergine (`#4A154B`) or its four hash colours
(`#36C5F0 #2EB67D #ECB22E #E01E5A`), and the teal is well away from Duolingo
green (`#58CC02`).

### Transparent variants
The mark is mostly white, so a single transparent PNG can't work on both
surfaces. Two are provided, both without the ground shadow:
- **on-dark:** white + brass, eyes deep teal, as in the icon.
- **on-light:** the white parts swapped to deep teal `#0B7A66` (shade `#075C4C`), brass and eyes unchanged.

### Monochrome
In a single colour the eyes would vanish into the dome, so the mono renders
knock them out in the background colour — negative space — and drop the blush.
The silhouette still reads as the smiling bell.

---

## Checks

- `checks/tiny-16-to-64.png` — 16 / 20 / 24 / 32 / 36 / 48 / 64 px, Lanczos-downsampled, shown at 3×.
- `checks/dark-light-modes.png` — Slack-style sidebar rows at 20 px (light `#F8F8F8`, dark `#19171D`) and 36–192 px tiles with the ~22 % mask applied.
- `checks/mono-white-on-deep.png`, `checks/mono-ink-on-white.png` — single-colour silhouette with knocked-out eyes.
- `checks/grayscale.png` — desaturated icon.

## Plain variant

`cufa-icon-plain-512.png` is the same construction without rows 7–8. Use it
where a face would be out of place — a favicon for an admin dashboard, a
print mark, a tiny 16 px context where the eyes are just a band anyway.
