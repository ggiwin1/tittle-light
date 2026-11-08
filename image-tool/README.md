# Tittle Image Studio

Turns a picture into an animation for the Tittle Light cube. The result is a `.json` file
that the desktop app imports from its **Custom Files** tab (Import JSON / BIN…).

## Install
    pip install pillow

## Use it
Double-click `tittle_image.py` (or run `python tittle_image.py`) to open the window. It has two tabs.

**From a picture** (free, offline): choose a picture, pick an effect and background, press
**Make animation**. The picture is shrunk to 8x8, a plain background is removed, and it plays
with one of 11 effects (static, spin, flip, scroll, marquee, wave, pulse, rain, explode,
extrude, orbit) over an optional background (sparkle, snow, fireflies, floor).
Animated GIFs keep their frames.

**With any AI** (ChatGPT, Gemini, Claude, Copilot … anything that accepts a picture):
1. Optionally type an idea ("make it wag its tail").
2. Press **Copy the prompt**.
3. Paste it into the AI chat, attach the picture, and send.
4. Paste the AI's whole answer into the box and press **Make animation**.

The AI designs an 8x8 pixel-art version of the picture, an optional second pose for blinking or
beating, and picks colours, an effect and a background. It only returns a small JSON recipe, and
this tool draws the animation, so the result always works on the cube. The prompt is also in
AI_PROMPT.md for copying by hand, and examples/example-ai-answer.txt shows what a good answer
looks like.

Then press **Save JSON…** and import the file in the desktop app.

## Command line

    python tittle_image.py dog.png --effect spin --background sparkle --preview dog.gif
    python tittle_image.py --prompt --idea "make it wag its tail"      # print the AI prompt
    python tittle_image.py --recipe answer.txt --preview dog.gif       # AI answer -> animation
    python tittle_image.py --list-effects

The recipe is stored inside the saved JSON (`source.recipe`), so you can tweak colours or
pixels by hand and re-render it with `--recipe`.
