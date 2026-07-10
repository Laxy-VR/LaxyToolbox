"""Brand theme for the app: palette, fonts, and a CustomTkinter theme override.

Colours come from the website's design tokens. CustomTkinter stores colours as
[light, dark] pairs; the app is dark-only, so we set both entries the same.
"""

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
BG = "#1e202c"           # --color-bg      : window background
SURFACE = "#2a2b3a"      # --color-surface : cards
SURFACE2 = "#35364a"     # derived         : inputs / hover surface
BORDER = "#3d3e56"       # derived         : subtle separators
ACCENT = "#60519b"       # --color-accent  : buttons, highlights
ACCENT_HOVER = "#6d5eae"  # derived        : lighter accent
ACCENT_PRESS = "#4e4180"  # derived        : darker accent
TEXT = "#bfc0d1"         # --color-text    : primary text
TEXT_MUTED = "#83849b"   # derived         : secondary text
ON_ACCENT = "#f3f2fb"    # text on accent buttons
SUCCESS = "#5fb389"
ERROR = "#d76d80"
WARNING = "#d9a441"

# Preferred brand fonts, with fallbacks for machines that don't have them.
# "DM Sans 14pt" is how GDI names the bundled variable font's default instance.
_FONT_PREFS = {
    "sans": (["DM Sans", "DM Sans 14pt"], "Segoe UI"),
    "mono": (["IBM Plex Mono"], "Consolas"),
    "heading": (["JetBrains Mono", "IBM Plex Mono"], "Consolas"),
}


def _dual(color: str):
    return [color, color]


def apply_theme():
    """Set appearance + override the built-in theme's colours. Call before
    creating the root window."""
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
