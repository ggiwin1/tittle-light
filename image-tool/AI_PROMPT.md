# Prompt for making a new cube light mode with any AI

Copy everything below the line into ChatGPT, Gemini, Claude or any AI chat that accepts pictures, attach a picture, and send it. To steer it, add a line like `What I'd like: make it wag its tail` before the "Reply with ONLY" part. Then paste the AI's whole answer into the "With any AI" tab of Tittle Image Studio (or save it as a .txt and run `python tittle_image.py --recipe answer.txt`).

---

I have a Tittle Light: a cube of 8 x 8 x 8 RGB LEDs. I want a new light mode for it based on the picture I'm attaching.

Please design an 8x8 pixel-art version of the main subject of the picture (think emoji or retro video game icon: bold simple shape, clear outline, 2 to 5 colours, no fine detail). Optionally draw a second pose of it so the animation can alternate between the two (blinking eyes, a beating heart, a wagging tail, a flickering flame). Then choose how it moves and what is behind it.

Tips for LEDs: bright, saturated colours look best. Black and very dark colours just show as "off", so use "." for off instead. Leave a little empty margin so the shape reads clearly.

Effects (how the picture moves inside the cube):
- static: Picture stands still in the middle of the cube (background still animates)
- spin: Picture spins around the cube's vertical axis
- flip: Picture tumbles forward around the horizontal axis
- scroll: Picture glides from the front of the cube to the back and returns
- marquee: Picture scrolls sideways across the front face
- wave: Picture ripples like a flag
- pulse: Picture breathes: it fades in and out and grows thicker
- rain: Picture builds itself from voxels falling from the top, then melts away
- explode: Picture bursts apart into flying voxels and pulls back together
- extrude: Bright parts of the picture pop out toward you in 3D, gently rocking
- orbit: Picture is wrapped around the four side faces and rotates

Backgrounds (what is lit around it):
- none: Nothing behind the picture
- sparkle: Random twinkling stars
- snow: Slowly falling snow
- fireflies: Drifting glowing dots
- floor: A glowing floor that ripples

Reply with ONLY a JSON object in exactly this shape, nothing before or after it:

{
  "name": "Short fun name",
  "description": "One sentence describing the animation",
  "palette": {"r": "#ff2020", "w": "#ffffff", "y": "#ffd000"},
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
}

Rules:
- "sprite" (and "sprite2" if you use it) is exactly 8 rows, top to bottom, each exactly 8 characters.
- Each character is "." (off) or a single-letter key from "palette". Every key you use must be in "palette".
- "sprite2" is either [] or a second pose in the same format.
- "effect" must be one of: static, spin, flip, scroll, marquee, wave, pulse, rain, explode, extrude, orbit.
- "background" must be one of: none, sparkle, snow, fireflies, floor.
- "num_frames" is 16 to 80, "interval_ms" is 40 to 250 (smaller is faster).

