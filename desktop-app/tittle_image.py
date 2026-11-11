#!/usr/bin/env python3
"""
Tittle Image Studio: turn a picture into an animation for the Tittle Light 8x8x8 cube.

Two ways to make an animation:

  From a picture   The image is shrunk to 8x8 pixels and played with one of the
                   built-in effects (spin, wave, rain, explode ...). Free, offline.

  With any AI      Copy the prompt this tool gives you, paste it into any AI chat
                   (ChatGPT, Gemini, Claude ...), attach a picture, and paste the AI's
                   answer back. The AI designs an 8x8 pixel-art sprite (plus an optional
                   second pose for blinking or beating), colours, an effect and a
                   background; this script draws the animation from that recipe, so the
                   result always works on the cube. No API key needed.

Either way the result is a .json file in the tittle-animation format that the
desktop app's "Custom Files" tab imports ("Import JSON / BIN..."), plus an
optional preview GIF.

Usage
    python tittle_image.py                          # opens the window
    python tittle_image.py photo.png -o out.json    # picture -> animation, effect "spin"
    python tittle_image.py photo.png --effect wave --preview out.gif
    python tittle_image.py --prompt --idea "make it wag its tail"   # print the AI prompt
    python tittle_image.py --recipe ai_answer.txt --preview out.gif # AI answer -> animation
    python tittle_image.py --list-effects

Requirements: Pillow (pip install pillow).
"""

import argparse
import json
import math
import os
import random
import sys

try:
    from PIL import Image, ImageDraw, ImageSequence
except ImportError:  # pragma: no cover
    print("Pillow is required: pip install pillow", file=sys.stderr)
    raise

N = 8
FRAME_BYTES = N * N * N * 3

EFFECTS = {
    "static":   "Picture stands still in the middle of the cube (background still animates)",
    "spin":     "Picture spins around the cube's vertical axis",
    "flip":     "Picture tumbles forward around the horizontal axis",
    "scroll":   "Picture glides from the front of the cube to the back and returns",
    "marquee":  "Picture scrolls sideways across the front face",
    "wave":     "Picture ripples like a flag",
    "pulse":    "Picture breathes: it fades in and out and grows thicker",
    "rain":     "Picture builds itself from voxels falling from the top, then melts away",
    "explode":  "Picture bursts apart into flying voxels and pulls back together",
    "extrude":  "Bright parts of the picture pop out toward you in 3D, gently rocking",
    "orbit":    "Picture is wrapped around the four side faces and rotates",
}
BACKGROUNDS = {
    "none":      "Nothing behind the picture",
    "sparkle":   "Random twinkling stars",
    "snow":      "Slowly falling snow",
    "fireflies": "Drifting glowing dots",
    "floor":     "A glowing floor that ripples",
}


# ─────────────────────────── Voxel frames ───────────────────────────

def vidx(x, y, z):
    """Cube voxel index: x left->right, y front->back, z bottom->top."""
    return z * 64 + y * 8 + x


class Frame:
    def __init__(self):
        self.buf = bytearray(FRAME_BYTES)

    def set(self, x, y, z, rgb, mode="max"):
        x, y, z = int(round(x)), int(round(y)), int(round(z))
        if not (0 <= x < N and 0 <= y < N and 0 <= z < N):
            return
        i = vidx(x, y, z) * 3
        r, g, b = (max(0, min(255, int(c))) for c in rgb)
        if mode == "max":
            self.buf[i] = max(self.buf[i], r)
            self.buf[i + 1] = max(self.buf[i + 1], g)
            self.buf[i + 2] = max(self.buf[i + 2], b)
        else:
            self.buf[i:i + 3] = bytes((r, g, b))


def scale(rgb, k):
    return tuple(max(0, min(255, int(c * k))) for c in rgb)


def is_lit(rgb, threshold=24):
    return rgb is not None and max(rgb) >= threshold


# ─────────────────────────── Sprites (8x8 pictures) ───────────────────────────
# A sprite is a list of 8 rows (top to bottom) of 8 (r, g, b) tuples or None (off).

def image_to_sprites(img, remove_background=True, boost=True, max_frames=24):
    """Shrink an image (or each frame of an animated GIF) to 8x8 sprites."""
    frames = []
    for fr in ImageSequence.Iterator(img):
        frames.append(fr.convert("RGBA"))
        if len(frames) >= max_frames:
            break
    sprites = [_one_image_to_sprite(f, remove_background, boost) for f in frames]
    return sprites


def _one_image_to_sprite(img, remove_background, boost):
    # Crop to a square around the centre so the subject keeps its shape.
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    small = img.resize((N, N), Image.LANCZOS)
    px = [[small.getpixel((c, r)) for c in range(N)] for r in range(N)]

    # Background guess: the most common corner colour (only for opaque images).
    bg = None
    if remove_background:
        corners = [px[0][0], px[0][N - 1], px[N - 1][0], px[N - 1][N - 1]]
        opaque = [c for c in corners if c[3] > 200]
        if len(opaque) >= 3:
            bg = max(opaque, key=lambda c: sum(_close(c, o) for o in opaque))

    sprite = []
    for r in range(N):
        row = []
        for c in range(N):
            R, G, B, A = px[r][c]
            if A < 100 or (bg is not None and _close((R, G, B, A), bg, 60)):
                row.append(None)
                continue
            rgb = (R, G, B)
            if boost:
                rgb = _boost(rgb)
            row.append(rgb if is_lit(rgb) else None)
        sprite.append(row)
    if all(p is None for row in sprite for p in row):  # removed everything: keep the raw picture
        return _one_image_to_sprite(img, False, boost)
    return sprite


def _close(a, b, tol=40):
    return all(abs(int(a[i]) - int(b[i])) <= tol for i in range(3))


def _boost(rgb):
    """LEDs look washed out with photo colours: push saturation and brightness."""
    r, g, b = rgb
    mx = max(r, g, b)
    if mx == 0:
        return (0, 0, 0)
    mn = min(r, g, b)
    sat = (mx - mn) / mx
    k = 255.0 / mx
    r, g, b = r * k, g * k, b * k
    if sat > 0.15:  # deepen colours, keep whites white
        avg = (r + g + b) / 3
        r, g, b = (avg + (c - avg) * 1.4 for c in (r, g, b))
    return tuple(max(0, min(255, int(c))) for c in (r, g, b))


def parse_color(s):
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def sprite_from_text(rows, palette):
    """rows: 8 strings of 8 chars; palette: {char: '#rrggbb'}. '.' or ' ' means off."""
    sprite = []
    for r in range(N):
        line = rows[r] if r < len(rows) else ""
        line = (line + "." * N)[:N]
        row = []
        for ch in line:
            row.append(parse_color(palette[ch]) if ch in palette and ch not in ". " else None)
        sprite.append(row)
    return sprite


def sprite_pixels(sprite):
    for r in range(N):
        for c in range(N):
            if sprite[r][c] is not None:
                yield c, N - 1 - r, sprite[r][c]   # (u = x, w = z, colour)


# ─────────────────────────── Effects ───────────────────────────

def sprite_for_frame(sprites, t, hold):
    """Pick the sprite pose for frame t (each pose shown for `hold` frames)."""
    return sprites[(t // max(1, hold)) % len(sprites)]


def draw_effect(effect, fr, sprite, t, n, rng, state):
    p = t / n                       # 0..1 through the loop
    ang = 2 * math.pi * p
    c = 3.5
    if effect == "static":
        for u, w, rgb in sprite_pixels(sprite):
            fr.set(u, 3, w, rgb)
            fr.set(u, 4, w, scale(rgb, 0.35))
    elif effect == "spin":
        ca, sa = math.cos(ang), math.sin(ang)
        for u, w, rgb in sprite_pixels(sprite):
            for d in (-0.5, 0.5):   # two layers thick so it never vanishes edge-on
                x = c + (u - c) * ca - d * sa
                y = c + (u - c) * sa + d * ca
                fr.set(x, y, w, rgb)
    elif effect == "flip":
        ca, sa = math.cos(ang), math.sin(ang)
        for u, w, rgb in sprite_pixels(sprite):
            for d in (-0.5, 0.5):
                y = c + d * ca - (w - c) * sa
                z = c + d * sa + (w - c) * ca
                fr.set(u, y, z, rgb)
    elif effect == "scroll":
        depth = (N - 1) * (0.5 - 0.5 * math.cos(ang))
        for u, w, rgb in sprite_pixels(sprite):
            fr.set(u, depth, w, rgb)
            for k, fade in ((1, 0.3), (2, 0.1)):   # trail
                back = depth - k if math.sin(ang) > 0 else depth + k
                fr.set(u, back, w, scale(rgb, fade))
    elif effect == "marquee":
        shift = int(p * 2 * N)
        for u, w, rgb in sprite_pixels(sprite):
            x = (u - shift) % (2 * N)
            if x < N:
                fr.set(x, 0, w, rgb)
                fr.set(x, 1, w, scale(rgb, 0.3))
    elif effect == "wave":
        for u, w, rgb in sprite_pixels(sprite):
            y = c + 2.6 * math.sin(ang + u * 0.8)
            fr.set(u, y, w, rgb)
    elif effect == "pulse":
        k = 0.25 + 0.75 * (0.5 - 0.5 * math.cos(ang))
        thick = 1 + int(round(3 * k))
        for u, w, rgb in sprite_pixels(sprite):
            for d in range(thick):
                fade = k * (1.0 - d / (thick + 0.5))
                fr.set(u, 3.5 - 0.5 - d // 2 if d % 2 == 0 else 3.5 + 0.5 + d // 2, w, scale(rgb, fade))
    elif effect == "rain":
        # First half: voxels fall into place one column at a time. Second half: they drop away.
        if "delay" not in state:
            state["delay"] = {(u, w): rng.random() for u in range(N) for w in range(N)}
        for u, w, rgb in sprite_pixels(sprite):
            dly = state["delay"][(u, w)] * 0.3
            if p < 0.5:
                q = max(0.0, min(1.0, (p / 0.5 - dly) / 0.7))
                z = w + (N - w) * (1 - q) ** 2
            else:
                q = max(0.0, min(1.0, ((p - 0.5) / 0.5 - dly) / 0.7))
                z = w - (w + 1) * q ** 2
            fr.set(u, 3, z, rgb)
            fr.set(u, 4, z, scale(rgb, 0.3))
    elif effect == "explode":
        if "vel" not in state:
            state["vel"] = {(u, w): (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
                            for u in range(N) for w in range(N)}
        burst = math.sin(math.pi * p) ** 2 * 5.0
        for u, w, rgb in sprite_pixels(sprite):
            vx, vy, vz = state["vel"][(u, w)]
            dx, dz = (u - c) * 0.35 + vx, (w - c) * 0.35 + vz
            fr.set(u + dx * burst, 3.5 + vy * burst, w + dz * burst, rgb)
    elif effect == "extrude":
        rock = 0.6 * math.sin(ang)
        ca, sa = math.cos(rock), math.sin(rock)
        for u, w, rgb in sprite_pixels(sprite):
            height = 1 + int(round(max(rgb) / 255 * 3 + (sum(rgb) / 765) * 2))
            for d in range(height):
                yy = 5.5 - d
                x = c + (u - c) * ca - (yy - c) * sa
                y = c + (u - c) * sa + (yy - c) * ca
                fr.set(x, y, w, scale(rgb, 1.0 if d == height - 1 else 0.45))
    elif effect == "orbit":
        # The picture is stretched around the 28 voxels of the cube's outer ring at each height.
        ring = ([(x, 0) for x in range(N)] + [(N - 1, y) for y in range(1, N)] +
                [(x, N - 1) for x in range(N - 2, -1, -1)] + [(0, y) for y in range(N - 2, 0, -1)])
        L = len(ring)
        off = int(p * L)
        for u, w, rgb in sprite_pixels(sprite):
            for rep in (0, L // 2):     # shown twice, on opposite sides
                x, y = ring[(u + off + rep) % L]
                fr.set(x, y, w, rgb)
    else:
        raise ValueError(f"unknown effect {effect!r}")


def draw_background(bg, fr, t, n, rng, state, color):
    if bg == "none":
        return
    p = t / n
    if bg == "sparkle":
        if "stars" not in state:
            state["stars"] = [(rng.randrange(N), rng.randrange(N), rng.randrange(N), rng.random())
                              for _ in range(14)]
        for x, y, z, ph in state["stars"]:
            k = max(0.0, math.sin(2 * math.pi * (p * 2 + ph)))
            fr.set(x, y, z, scale(color, k ** 3 * 0.8))
    elif bg == "snow":
        if "flakes" not in state:
            state["flakes"] = [(rng.randrange(N), rng.randrange(N), rng.random()) for _ in range(12)]
        for x, y, ph in state["flakes"]:
            z = (N - 1) - ((p * 2 + ph) % 1.0) * N
            fr.set(x, y, z, scale(color, 0.45))
    elif bg == "fireflies":
        if "flies" not in state:
            state["flies"] = [(rng.uniform(1, 6), rng.uniform(1, 6), rng.uniform(1, 6), rng.random(), rng.randint(1, 3))
                              for _ in range(6)]
        for x, y, z, ph, sp in state["flies"]:
            a = 2 * math.pi * (p * sp + ph)
            fr.set(x + 1.5 * math.cos(a), y + 1.5 * math.sin(a), z + math.sin(2 * a),
                   scale(color, 0.5 + 0.5 * math.sin(a * 3)))
    elif bg == "floor":
        for x in range(N):
            for y in range(N):
                d = math.hypot(x - 3.5, y - 3.5)
                k = 0.5 + 0.5 * math.sin(d * 1.3 - 2 * math.pi * p * 2)
                fr.set(x, y, 0, scale(color, 0.08 + 0.3 * k))
    else:
        raise ValueError(f"unknown background {bg!r}")


def render(sprites, effect="spin", background="none", bg_color=(60, 60, 120),
           num_frames=40, hold=None, seed=1):
    """Render sprites into a list of 1536-byte frames."""
    if effect not in EFFECTS:
        raise ValueError(f"unknown effect {effect!r}; choose from {', '.join(EFFECTS)}")
    if background not in BACKGROUNDS:
        raise ValueError(f"unknown background {background!r}; choose from {', '.join(BACKGROUNDS)}")
    num_frames = max(2, min(200, int(num_frames)))
    if hold is None:
        hold = max(1, num_frames // (len(sprites) * 2)) if len(sprites) > 1 else num_frames
    rng = random.Random(seed)
    fx_state, bg_state = {}, {}
    frames = []
    for t in range(num_frames):
        fr = Frame()
        draw_background(background, fr, t, num_frames, rng, bg_state, bg_color)
        draw_effect(effect, fr, sprite_for_frame(sprites, t, hold), t, num_frames, rng, fx_state)
        frames.append(bytes(fr.buf))
    return frames


# ─────────────────────────── Output ───────────────────────────

def animation_json(name, frames, interval_ms=80, intensity=255, extra=None):
    obj = {
        "format": "tittle-animation",
        "version": 1,
        "name": name,
        "interval_ms": int(interval_ms),
        "intensity": int(intensity),
        "opcode": "0x90",
        "voxel_index": "z*64 + y*8 + x",
        "encoding": "hex",
        "frames": [f.hex() for f in frames],
    }
    if extra:
        obj["source"] = extra
    return obj


def save_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)


def preview_image(frame, size=260):
    """Draw one frame as a 3D-ish view of the cube (back voxels first)."""
    img = Image.new("RGB", (size, size), (12, 12, 16))
    d = ImageDraw.Draw(img)
    step = size / 13.0
    ox, oy = size * 0.18, size * 0.86
    pts = []
    for z in range(N):
        for y in range(N):
            for x in range(N):
                i = vidx(x, y, z) * 3
                rgb = tuple(frame[i:i + 3])
                sx = ox + x * step + y * step * 0.5
                sy = oy - z * step - y * step * 0.32
                pts.append((y, -z, sx, sy, rgb))
    pts.sort(key=lambda p: (-p[0], p[1]))
    r_off = step * 0.16
    r_on = step * 0.38
    for _, _, sx, sy, rgb in pts:      # unlit LEDs first so they never cover lit ones
        if max(rgb) < 8:
            d.ellipse((sx - r_off, sy - r_off, sx + r_off, sy + r_off), fill=(34, 34, 42))
    for _, _, sx, sy, rgb in pts:
        if max(rgb) >= 8:
            d.ellipse((sx - r_on, sy - r_on, sx + r_on, sy + r_on), fill=rgb, outline=scale(rgb, 0.5))
    return img


def save_preview_gif(frames, path, interval_ms=80, size=260):
    imgs = [preview_image(f, size) for f in frames]
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=max(20, interval_ms), loop=0)



# ─────────────────────────── AI recipes (any AI chat tool) ───────────────────────────
# The AI never makes the animation itself. It answers AI_PROMPT with a small JSON
# "recipe" (an 8x8 pixel-art sprite, colours, effect, background) and this script
# draws the frames, so whatever AI is used the output is always a valid cube file.

AI_PROMPT = """I have a Tittle Light: a cube of 8 x 8 x 8 RGB LEDs. I want a new light mode for it {source}

Please design an 8x8 pixel-art version of {subject} (think emoji or retro video game icon: bold simple shape, clear outline, 2 to 5 colours, no fine detail). Optionally draw a second pose of it so the animation can alternate between the two (blinking eyes, a beating heart, a wagging tail, a flickering flame). Then choose how it moves and what is behind it.

Tips for LEDs: bright, saturated colours look best. Black and very dark colours just show as "off", so use "." for off instead. Leave a little empty margin so the shape reads clearly.

Effects (how the picture moves inside the cube):
{effects}

Backgrounds (what is lit around it):
{backgrounds}
{idea}
Reply with ONLY a JSON object in exactly this shape, nothing before or after it:

{{
  "name": "Short fun name",
  "description": "One sentence describing the animation",
  "palette": {{"r": "#ff2020", "w": "#ffffff", "y": "#ffd000"}},
  "sprite": [
    "........",
    "..rr.rr.",
    ".rrrrrrr",
    ".rrwrrrr",
    "..rrrrr.",
    "...rrr..",
    "....r...",
    "........"
  ],
  "sprite2": [],
  "effect": "spin",
  "background": "sparkle",
  "background_color": "#2030a0",
  "num_frames": 40,
  "interval_ms": 80
}}

Rules:
- "sprite" (and "sprite2" if you use it) is exactly 8 rows, top to bottom, each exactly 8 characters.
- Each character is "." (off) or a single-letter key from "palette". Every key you use must be in "palette".
- "sprite2" is either [] or a second pose in the same format.
- "effect" must be one of: {effect_names}.
- "background" must be one of: {background_names}.
- "num_frames" is 16 to 80, "interval_ms" is 40 to 250 (smaller is faster).
"""


def build_prompt(idea="", picture=True):
    """picture=True: the user attaches a picture to the AI chat. Without one, the idea text is the whole brief."""
    idea = (idea or "").strip()
    if picture:
        source = "based on the picture I'm attaching."
        subject = "the main subject of the picture"
        idea_line = f"\nWhat I'd like: {idea}\n" if idea else ""
    else:
        source = f"based on this idea: {idea or 'surprise me with something fun'}"
        subject = "that idea"
        idea_line = ""
    return AI_PROMPT.format(
        source=source, subject=subject,
        effects="\n".join(f"- {k}: {v}" for k, v in EFFECTS.items()),
        backgrounds="\n".join(f"- {k}: {v}" for k, v in BACKGROUNDS.items()),
        effect_names=", ".join(EFFECTS),
        background_names=", ".join(BACKGROUNDS),
        idea=idea_line,
    )


def extract_recipe(text):
    """Find the recipe JSON in an AI's reply, even with prose or ``` fences around it."""
    if isinstance(text, dict):
        return text
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    dec = json.JSONDecoder()
    best = None
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "sprite" in obj:
            return obj
        best = best or (obj if isinstance(obj, dict) else None)
    if best is None:
        raise ValueError("Couldn't find the JSON recipe in that text. Paste the AI's whole answer.")
    raise ValueError("The JSON in that answer has no \"sprite\". Ask the AI to follow the format exactly.")


def _rows(v):
    """Accept rows as strings or as lists of single characters."""
    if not v:
        return []
    rows = []
    for r in v:
        rows.append("".join(str(c) for c in r) if isinstance(r, (list, tuple)) else str(r).replace(" ", "."))
    return rows


def recipe_to_animation(recipe, seed=1):
    """Turn an AI (or hand-written) recipe into (name, frames, interval_ms)."""
    recipe = recipe.get("source", {}).get("recipe", recipe) if isinstance(recipe.get("source"), dict) else recipe
    pal_in = recipe.get("palette", {})
    if isinstance(pal_in, list):   # also accept [{"key": "r", "color": "#ff0000"}, ...]
        pal_in = {e.get("key", ""): e.get("color", "") for e in pal_in if isinstance(e, dict)}
    palette = {}
    for k, col in pal_in.items():
        k = str(k)[:1]
        if k and k not in ". ":
            try:
                palette[k] = "#%02x%02x%02x" % parse_color(str(col))
            except Exception:
                pass
    rows = _rows(recipe.get("sprite"))
    if not rows:
        raise ValueError("Recipe has no sprite rows")
    sprites = [sprite_from_text(rows, palette)]
    rows2 = _rows(recipe.get("sprite2"))
    if rows2:
        sprites.append(sprite_from_text(rows2, palette))
    if all(p is None for row in sprites[0] for p in row):
        raise ValueError("The sprite is empty: none of its letters are in the palette")
    effect = str(recipe.get("effect", "spin")).lower()
    effect = effect if effect in EFFECTS else "spin"
    bg = str(recipe.get("background", "none")).lower()
    bg = bg if bg in BACKGROUNDS else "none"
    try:
        bg_color = parse_color(str(recipe.get("background_color", "#303070")))
    except Exception:
        bg_color = (48, 48, 112)
    try:
        n = max(16, min(80, int(recipe.get("num_frames", 40))))
        interval = max(40, min(250, int(recipe.get("interval_ms", 80))))
    except (TypeError, ValueError):
        n, interval = 40, 80
    frames = render(sprites, effect, bg, bg_color, n, seed=seed)
    return str(recipe.get("name") or "AI light mode")[:40], frames, interval


def make_from_recipe(text_or_obj):
    recipe = extract_recipe(text_or_obj)
    name, frames, interval = recipe_to_animation(recipe)
    extra = {"tool": "tittle_image.py", "mode": "ai-recipe", "recipe": recipe}
    return animation_json(name, frames, interval, extra=extra), frames, recipe


# ─────────────────────────── Offline ───────────────────────────

def make_offline(image_path, effect="spin", background="none", bg_color="#303070",
                 num_frames=40, interval_ms=80, keep_background=False, name=None):
    img = Image.open(image_path)
    sprites = image_to_sprites(img, remove_background=not keep_background)
    frames = render(sprites, effect, background, parse_color(bg_color), num_frames)
    name = name or os.path.splitext(os.path.basename(image_path))[0]
    extra = {"tool": "tittle_image.py", "mode": "offline", "effect": effect, "background": background}
    return animation_json(name, frames, interval_ms, extra=extra), frames


# ─────────────────────────── Window ───────────────────────────

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    try:
        from PIL import ImageTk
    except ImportError:
        ImageTk = None

    root = tk.Tk()
    root.title("Tittle Image Studio")
    fg, bg = "#e8e8ee", "#1e1e24"
    root.configure(bg=bg)
    st = {"image": None, "result": None, "frames": None, "anim_job": None, "tk_imgs": [], "thumb": None}
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass

    def label(parent, text, **kw):
        kw.setdefault("fg", fg)
        return tk.Label(parent, text=text, bg=bg, **kw)

    left = tk.Frame(root, bg=bg, padx=12, pady=12)
    left.pack(side=tk.LEFT, fill=tk.Y)
    right = tk.Frame(root, bg=bg, padx=12, pady=12)
    right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    tabs = ttk.Notebook(left)
    tabs.pack(fill=tk.BOTH, expand=True)
    t_off = tk.Frame(tabs, bg=bg, padx=8, pady=8)
    t_ai = tk.Frame(tabs, bg=bg, padx=8, pady=8)
    tabs.add(t_off, text="From a picture")
    tabs.add(t_ai, text="With any AI")

    status = label(left, "", wraplength=300, justify="left", fg="#9cf")

    def show_result(obj, frames, msg):
        st["result"], st["frames"] = obj, frames
        status.config(text=msg)
        play_preview(frames, obj["interval_ms"])

    # --- Tab 1: offline conversion
    label(t_off, "Turn a picture straight into an animation. Free and instant.",
          wraplength=300, justify="left").pack(anchor="w")
    img_label = label(t_off, "No picture yet")
    thumb = tk.Label(t_off, bg=bg)

    def pick_image():
        p = filedialog.askopenfilename(title="Choose a picture",
                                       filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                                                  ("All files", "*.*")])
        if not p:
            return
        st["image"] = p
        img_label.config(text=os.path.basename(p))
        if ImageTk:
            im = Image.open(p).convert("RGB")
            im.thumbnail((140, 140))
            st["thumb"] = ImageTk.PhotoImage(im)
            thumb.config(image=st["thumb"])

    tk.Button(t_off, text="Choose picture…", command=pick_image).pack(anchor="w", pady=(8, 2))
    img_label.pack(anchor="w")
    thumb.pack(anchor="w", pady=4)

    opts = tk.Frame(t_off, bg=bg)
    opts.pack(anchor="w", fill=tk.X)
    label(opts, "Effect").grid(row=0, column=0, sticky="w")
    effect = ttk.Combobox(opts, values=list(EFFECTS), state="readonly", width=14)
    effect.set("spin")
    effect.grid(row=0, column=1, sticky="w", padx=4, pady=2)
    label(opts, "Background").grid(row=1, column=0, sticky="w")
    background = ttk.Combobox(opts, values=list(BACKGROUNDS), state="readonly", width=14)
    background.set("sparkle")
    background.grid(row=1, column=1, sticky="w", padx=4, pady=2)
    keep_bg = tk.BooleanVar(value=False)
    tk.Checkbutton(opts, text="Keep the picture's background", variable=keep_bg, bg=bg, fg=fg,
                   selectcolor="#333", activebackground=bg).grid(row=2, column=0, columnspan=2, sticky="w")
    fx_help = label(opts, EFFECTS["spin"], wraplength=280, justify="left", fg="#9aa")
    fx_help.grid(row=3, column=0, columnspan=2, sticky="w")
    effect.bind("<<ComboboxSelected>>", lambda e: fx_help.config(text=EFFECTS[effect.get()]))

    def make_offline_clicked():
        if not st["image"]:
            messagebox.showinfo("Pick a picture", "Choose a picture first.")
            return
        try:
            obj, frames = make_offline(st["image"], effect.get(), background.get(),
                                       keep_background=keep_bg.get())
        except Exception as e:
            messagebox.showerror("Could not convert", str(e))
            return
        show_result(obj, frames, f"Made '{obj['name']}' ({len(frames)} frames).")

    tk.Button(t_off, text="✨ Make animation", command=make_offline_clicked,
              font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 0))

    # --- Tab 2: any AI chat tool
    label(t_ai, "1. Optional: what should the AI make of it?").pack(anchor="w")
    idea = tk.Entry(t_ai, width=40)
    idea.pack(anchor="w", pady=(0, 6))

    def copy_prompt():
        root.clipboard_clear()
        root.clipboard_append(build_prompt(idea.get()))
        status.config(text="Prompt copied. Paste it into ChatGPT, Gemini, Claude or any AI chat, "
                           "attach your picture, and send it.")

    tk.Button(t_ai, text="📋 2. Copy the prompt", command=copy_prompt).pack(anchor="w")
    label(t_ai, "3. Paste it into any AI chat together with your picture.",
          wraplength=300, justify="left").pack(anchor="w", pady=(6, 0))
    label(t_ai, "4. Paste the AI's whole answer here:").pack(anchor="w", pady=(6, 0))
    answer = tk.Text(t_ai, width=40, height=12, bg="#111116", fg=fg, insertbackground=fg)
    answer.pack(anchor="w", fill=tk.BOTH, expand=True)

    def make_from_answer():
        text = answer.get("1.0", tk.END)
        if not text.strip():
            messagebox.showinfo("Nothing pasted", "Paste the AI's answer into the box first.")
            return
        try:
            obj, frames, recipe = make_from_recipe(text)
        except Exception as e:
            messagebox.showerror("Couldn't read that answer", str(e))
            return
        show_result(obj, frames, f"'{obj['name']}': {recipe.get('description', '')}")

    tk.Button(t_ai, text="✨ 5. Make animation", command=make_from_answer,
              font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 0))

    # --- Save + preview
    def save():
        if not st["result"]:
            messagebox.showinfo("Nothing yet", "Make an animation first.")
            return
        safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in st["result"]["name"]).strip() or "animation"
        p = filedialog.asksaveasfilename(title="Save animation", defaultextension=".json",
                                         initialfile=f"{safe}.json", filetypes=[("Tittle animation", "*.json")])
        if not p:
            return
        save_json(st["result"], p)
        status.config(text=f"Saved {os.path.basename(p)}. In the desktop app, open the Custom Files tab "
                           "and use 'Import JSON / BIN…'.")
        if messagebox.askyesno("Saved", "Also save a preview GIF next to it?"):
            save_preview_gif(st["frames"], os.path.splitext(p)[0] + ".gif", st["result"]["interval_ms"])

    tk.Button(left, text="💾 Save JSON…", command=save).pack(anchor="w", pady=(10, 4))
    status.pack(anchor="w", pady=4)

    label(right, "Preview", font=("Segoe UI", 11, "bold")).pack(anchor="w")
    canvas = tk.Label(right, bg="#0c0c10", width=340, height=340)
    canvas.pack()

    def play_preview(frames, interval):
        if st["anim_job"]:
            root.after_cancel(st["anim_job"])
        if not ImageTk:
            return
        imgs = [ImageTk.PhotoImage(preview_image(f, 340)) for f in frames]
        st["tk_imgs"] = imgs

        def step(i=0):
            canvas.config(image=imgs[i % len(imgs)])
            st["anim_job"] = root.after(max(20, interval), step, i + 1)
        step()

    root.mainloop()


# ─────────────────────────── Command line ───────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="Turn a picture into a Tittle Light cube animation (.json).")
    ap.add_argument("image", nargs="?", help="picture to convert (png, jpg, gif ...); omit to open the window")
    ap.add_argument("-o", "--output", help="output .json (default: next to the input)")
    ap.add_argument("--effect", default="spin", choices=list(EFFECTS))
    ap.add_argument("--background", default="none", choices=list(BACKGROUNDS))
    ap.add_argument("--bg-color", default="#303070")
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--interval", type=int, default=80, help="ms per frame")
    ap.add_argument("--keep-background", action="store_true", help="don't remove the picture's background")
    ap.add_argument("--name")
    ap.add_argument("--preview", help="also write a preview .gif")
    ap.add_argument("--prompt", action="store_true", help="print the prompt to paste into any AI chat, then exit")
    ap.add_argument("--idea", default="", help="with --prompt: what you'd like the AI to make of the picture")
    ap.add_argument("--recipe", help="text file with the AI's answer (or a saved recipe) to turn into an animation")
    ap.add_argument("--list-effects", action="store_true")
    a = ap.parse_args(argv)

    if a.list_effects:
        print("Effects:")
        for k, v in EFFECTS.items():
            print(f"  {k:9} {v}")
        print("Backgrounds:")
        for k, v in BACKGROUNDS.items():
            print(f"  {k:9} {v}")
        return 0
    if a.prompt:
        print(build_prompt(a.idea))
        return 0

    if a.recipe:
        with open(a.recipe, "r", encoding="utf-8") as f:
            obj, frames, recipe = make_from_recipe(f.read())
        print(f"{obj['name']}: {recipe.get('description', '')}")
        out = a.output or os.path.splitext(a.recipe)[0] + ".tittle.json"
    elif not a.image:
        run_gui()
        return 0
    else:
        obj, frames = make_offline(a.image, a.effect, a.background, a.bg_color, a.frames, a.interval,
                                   a.keep_background, a.name)
        out = a.output or os.path.splitext(a.image)[0] + ".json"

    save_json(obj, out)
    print(f"Wrote {out} ({len(frames)} frames, {obj['interval_ms']} ms each)")
    if a.preview:
        save_preview_gif(frames, a.preview, obj["interval_ms"])
        print(f"Wrote preview {a.preview}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
