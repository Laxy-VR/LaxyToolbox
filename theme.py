"""Brand theme for the app: palette, fonts, and a CustomTkinter theme override.

Colours come from the website's design tokens. CustomTkinter stores colours as
[light, dark] pairs; the app is dark-only, so we set both entries the same.
"""

import colorsys
import os
import sys
import tkinter.font as tkfont

import customtkinter as ctk


def load_brand_fonts(fonts_dir: str) -> None:
    """Register the bundled brand TTFs privately for this process (Windows).

    Must run before any Tk window queries font families. The fonts stay
    invisible to other apps and vanish when the process exits.
    """
    if sys.platform != "win32" or not os.path.isdir(fonts_dir):
        return
    import ctypes
    FR_PRIVATE = 0x10
    for name in os.listdir(fonts_dir):
        if name.lower().endswith((".ttf", ".otf")):
            try:
                ctypes.windll.gdi32.AddFontResourceExW(
                    os.path.join(fonts_dir, name), FR_PRIVATE, 0)
            except Exception:  # noqa: BLE001 - fall back to system fonts
                pass

# --- Palette (from the site's @theme tokens, plus a few derived shades) ---
# The "neutrals" are deliberately not grey: they carry a whisper of the accent
# hue. They're authored for the purple brand accent below; apply_theme()
# rotates their hue to follow whichever accent is chosen, keeping lightness
# and saturation identical, so a green app gets green-tinted darks instead of
# purple ones peeking through.
_BASE_NEUTRALS = {
    "BG": "#1e202c",          # --color-bg      : window background
    "SURFACE": "#2a2b3a",     # --color-surface : cards
    "SURFACE2": "#35364a",    # derived         : inputs / hover surface
    "BORDER": "#3d3e56",      # derived         : subtle separators
    "TEXT": "#bfc0d1",        # --color-text    : primary text
    "TEXT_MUTED": "#83849b",  # derived         : secondary text
    "ON_ACCENT": "#f3f2fb",   # text on accent buttons
}
BG = _BASE_NEUTRALS["BG"]
SURFACE = _BASE_NEUTRALS["SURFACE"]
SURFACE2 = _BASE_NEUTRALS["SURFACE2"]
BORDER = _BASE_NEUTRALS["BORDER"]
TEXT = _BASE_NEUTRALS["TEXT"]
TEXT_MUTED = _BASE_NEUTRALS["TEXT_MUTED"]
ON_ACCENT = _BASE_NEUTRALS["ON_ACCENT"]
SUCCESS = "#5fb389"   # semantic colours stay fixed across accents
ERROR = "#d76d80"
WARNING = "#d9a441"


def _hex_to_rgb(color: str):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _rotate_hue(color: str, delta: float) -> str:
    """The same colour with its hue rotated by `delta` (0..1 wraps)."""
    h, l, s = colorsys.rgb_to_hls(*_hex_to_rgb(color))
    rgb = colorsys.hls_to_rgb((h + delta) % 1.0, l, s)
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def _hue(color: str) -> float:
    return colorsys.rgb_to_hls(*_hex_to_rgb(color))[0]

# Accent choices, selectable in the app's settings (gear button). Each entry
# carries the button color, its hover/press shades, and the tinted header
# title / note text derived from it. Purple is the original brand accent.
ACCENTS = {
    "Purple": {"accent": "#60519b", "hover": "#6d5eae", "press": "#4e4180",
               "title": "#a99ce0", "note": "#9a8fd0"},
    "Blue":   {"accent": "#46699e", "hover": "#5578ad", "press": "#395682",
               "title": "#9cb8e0", "note": "#8da9d1"},
    "Green":  {"accent": "#47805c", "hover": "#568f6b", "press": "#3a694c",
               "title": "#9ccfae", "note": "#8dc0a0"},
    "Teal":   {"accent": "#3d7f84", "hover": "#4c8e93", "press": "#32686c",
               "title": "#96c8cc", "note": "#88b9bd"},
    "Rose":   {"accent": "#9c5470", "hover": "#ab637f", "press": "#80455c",
               "title": "#dba4b8", "note": "#cc96aa"},
    "Amber":  {"accent": "#98743d", "hover": "#a7834c", "press": "#7d5f32",
               "title": "#d9bb8a", "note": "#caac7c"},
}
ACCENT_NAME = "Purple"
ACCENT = ACCENTS["Purple"]["accent"]
ACCENT_HOVER = ACCENTS["Purple"]["hover"]
ACCENT_PRESS = ACCENTS["Purple"]["press"]
TITLE = ACCENTS["Purple"]["title"]
NOTE = ACCENTS["Purple"]["note"]

# Preferred brand fonts, with fallbacks for machines that don't have them.
# "DM Sans 14pt" is how GDI names the bundled variable font's default instance.
_FONT_PREFS = {
    "sans": (["DM Sans", "DM Sans 14pt"], "Segoe UI"),
    "mono": (["IBM Plex Mono"], "Consolas"),
    "heading": (["JetBrains Mono", "IBM Plex Mono"], "Consolas"),
}


def _dual(color: str):
    return [color, color]


def apply_theme(accent: str = "Purple"):
    """Set appearance + override the built-in theme's colours with the chosen
    accent. Call before creating the root window."""
    global ACCENT_NAME, ACCENT, ACCENT_HOVER, ACCENT_PRESS, TITLE, NOTE
    global BG, SURFACE, SURFACE2, BORDER, TEXT, TEXT_MUTED, ON_ACCENT
    a = ACCENTS.get(accent) or ACCENTS["Purple"]
    ACCENT_NAME = accent if accent in ACCENTS else "Purple"
    ACCENT, ACCENT_HOVER, ACCENT_PRESS = a["accent"], a["hover"], a["press"]
    TITLE, NOTE = a["title"], a["note"]
    # Rotate every neutral's hue by the same amount the accent moved from the
    # brand purple, so the whole canvas follows the accent (Purple: delta 0).
    delta = _hue(ACCENT) - _hue(ACCENTS["Purple"]["accent"])
    BG = _rotate_hue(_BASE_NEUTRALS["BG"], delta)
    SURFACE = _rotate_hue(_BASE_NEUTRALS["SURFACE"], delta)
    SURFACE2 = _rotate_hue(_BASE_NEUTRALS["SURFACE2"], delta)
    BORDER = _rotate_hue(_BASE_NEUTRALS["BORDER"], delta)
    TEXT = _rotate_hue(_BASE_NEUTRALS["TEXT"], delta)
    TEXT_MUTED = _rotate_hue(_BASE_NEUTRALS["TEXT_MUTED"], delta)
    ON_ACCENT = _rotate_hue(_BASE_NEUTRALS["ON_ACCENT"], delta)
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")  # gives us a full set of keys
    t = ctk.ThemeManager.theme

    def setc(section, field, color):
        if section in t and field in t[section]:
            t[section][field] = _dual(color)

    setc("CTk", "fg_color", BG)
    setc("CTkToplevel", "fg_color", BG)

    setc("CTkFrame", "fg_color", SURFACE)
    setc("CTkFrame", "top_fg_color", SURFACE)
    setc("CTkFrame", "border_color", BORDER)

    setc("CTkButton", "fg_color", ACCENT)
    setc("CTkButton", "hover_color", ACCENT_HOVER)
    setc("CTkButton", "text_color", ON_ACCENT)
    setc("CTkButton", "border_color", BORDER)

    setc("CTkLabel", "text_color", TEXT)

    setc("CTkEntry", "fg_color", SURFACE2)
    setc("CTkEntry", "border_color", BORDER)
    setc("CTkEntry", "text_color", TEXT)
    setc("CTkEntry", "placeholder_text_color", TEXT_MUTED)

    setc("CTkProgressBar", "fg_color", SURFACE2)
    setc("CTkProgressBar", "progress_color", ACCENT)

    setc("CTkSlider", "fg_color", SURFACE2)
    setc("CTkSlider", "progress_color", ACCENT)
    setc("CTkSlider", "button_color", ACCENT)
    setc("CTkSlider", "button_hover_color", ACCENT_HOVER)

    setc("CTkOptionMenu", "fg_color", SURFACE2)
    setc("CTkOptionMenu", "button_color", ACCENT)
    setc("CTkOptionMenu", "button_hover_color", ACCENT_HOVER)
    setc("CTkOptionMenu", "text_color", TEXT)

    setc("CTkSegmentedButton", "fg_color", SURFACE)
    setc("CTkSegmentedButton", "selected_color", ACCENT)
    setc("CTkSegmentedButton", "selected_hover_color", ACCENT_HOVER)
    setc("CTkSegmentedButton", "unselected_color", SURFACE2)
    setc("CTkSegmentedButton", "unselected_hover_color", BORDER)
    setc("CTkSegmentedButton", "text_color", TEXT)

    setc("CTkTextbox", "fg_color", SURFACE2)
    setc("CTkTextbox", "border_color", BORDER)
    setc("CTkTextbox", "text_color", TEXT)
    setc("CTkTextbox", "scrollbar_button_color", ACCENT)
    setc("CTkTextbox", "scrollbar_button_hover_color", ACCENT_HOVER)

    setc("CTkScrollableFrame", "label_fg_color", SURFACE)

    setc("CTkScrollbar", "button_color", ACCENT)
    setc("CTkScrollbar", "button_hover_color", ACCENT_HOVER)

    setc("DropdownMenu", "fg_color", SURFACE2)
    setc("DropdownMenu", "hover_color", ACCENT)
    setc("DropdownMenu", "text_color", TEXT)


def resolve_fonts(root) -> dict:
    """Return {role: family}, using brand fonts when installed, else fallbacks."""
    available = set(tkfont.families(root))
    fonts = {}
    for role, (preferred, fallback) in _FONT_PREFS.items():
        fonts[role] = next((f for f in preferred if f in available), fallback)
    return fonts


def set_default_font_family(family: str):
    """Make `family` the default for every widget created afterwards."""
    if "CTkFont" in ctk.ThemeManager.theme:
        ctk.ThemeManager.theme["CTkFont"]["family"] = family
