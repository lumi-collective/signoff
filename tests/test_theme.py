"""The page's colours: six palettes in light and dark as tokens, Catppuccin by default, WCAG contrast in each,
and a high-contrast set over all of them. The checks read page.html itself; theme_tokens.py only has to agree."""
import re
import unittest

import theme_tokens
from support import signoff

PAGE = (signoff.HERE / "page.html").read_text(encoding="utf-8").replace("\r\n", "\n")
START, END = "<!-- theme:", "<!-- /theme -->"
# The closed list: an id may be added, never renamed or removed (a saved choice names one).
PALETTES = ("catppuccin", "signoff", "rose-pine", "gruvbox", "tokyo-night", "dracula")
# (foreground token, background token, minimum): the pairs the page's rules put together, for example text on
# the selected row (sel), on a chip (hover), on a code block or table head (field), on the orange notice (boxsoft).
PAIRS = ([(t, s, 4.5) for t in ("text", "soft", "quiet") for s in ("bg", "chrome", "panel", "field", "sel", "hover")]
         + [(t, "boxsoft", 4.5) for t in ("text", "soft", "quiet")] + [("quiet", "oksoft", 4.5)]
         + [("accent", s, 4.5) for s in ("bg", "panel", "sel", "hover")]
         + [("btntext", "btn", 4.5), ("btntext", "accent", 4.5), ("btnofftext", "btnoff", 4.5)]
         + [("boxtext", s, 4.5) for s in ("bg", "panel", "field", "sel", "hover", "boxsoft")]
         + [("ok", s, 4.5) for s in ("bg", "panel", "sel", "hover", "oksoft")]
         + [(t, "panel", 3.0) for t in ("box", "focus", "mark")]
         + [("box", "bg", 3.0), ("focus", "bg", 3.0), ("focus", "chrome", 3.0), ("box", "chrome", 3.0),
            ("accent", "chrome", 4.5)]
         + [("m%dfg" % i, "m%dbg" % i, 4.5) for i in range(6)])
HIGH_PAIRS = [(a, b, 7.0 if m == 4.5 else m) for a, b, m in PAIRS] + [("line", s, 3.0) for s in ("bg", "panel")]


def contrast(a, b):
    def lum(h):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b_ = (ch(int(h[i:i + 2], 16)) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def rules(css, media=""):
    """(media, selector, {property: value}) for every rule, @media blocks opened one level."""
    out, i = [], 0
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    while True:
        open_ = css.find("{", i)
        if open_ < 0:
            return out
        head = css[i:open_].strip()
        depth, j = 1, open_ + 1
        while depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        body = css[open_ + 1:j - 1]
        if head.startswith("@media"):
            out += rules(body, head)
        else:
            decls = dict(d.split(":", 1) for d in body.split(";") if d.strip())
            for sel in head.split(","):
                out.append((media, sel.strip(), {k.strip(): v.strip() for k, v in decls.items()}))
        i = j


def theme_region():
    return PAGE[PAGE.index(START):PAGE.index(END) + len(END)]


def blocks():
    """(palette or None, mode) -> declarations, for the palette rules; the high-contrast rules separately."""
    region = theme_region()
    styles = re.findall(r"<style([^>]*)>(.*?)</style>", region, re.S)
    found, high = {}, []
    for attrs, css in styles:
        for media, sel, decls in rules(css):
            if "prefers-contrast" in media:
                high.append((media, sel, decls))
                continue
            palette = re.search(r"\[data-palette=([a-z-]+)\]", sel)
            dark = "[data-mode=dark]" in sel or ("prefers-color-scheme:dark" in media and ":not([data-mode=light])" in sel)
            key = (palette.group(1) if palette else None, "dark" if dark else "light")
            found.setdefault(key, []).append((media, sel, decls))
    return styles, found, high


class Theme(unittest.TestCase):
    def test_page_matches_the_generator(self):
        self.assertEqual(theme_region() + "\n", theme_tokens.css(), "paste the output of tests/theme_tokens.py")
        self.assertEqual(theme_tokens.PALETTE_IDS, PALETTES)
        self.assertEqual(signoff.PALETTES, PALETTES)  # the ids the server takes and keeps in prefs.json

    def test_a_palette_applies_to_any_element(self):
        # The picker's swatches carry data-palette and data-mode, so each shows its palette in its own colours.
        _, found, _ = blocks()
        for (p, mode), entries in found.items():
            for _, sel, _ in entries:
                if p:
                    self.assertTrue(sel.startswith("[data-palette=%s]" % p), sel)

    def test_palette_ids_are_the_closed_list_each_in_light_and_dark(self):
        _, found, _ = blocks()
        ids = {p for p, _ in found if p}
        self.assertEqual(ids, set(PALETTES))
        for p in PALETTES:
            for mode in ("light", "dark"):
                self.assertIn((p, mode), found)
        # A dark palette is the same whether the system asks for it or data-mode does.
        for (p, mode), entries in found.items():
            self.assertEqual(len({tuple(sorted(d.items())) for _, _, d in entries}), 1, (p, mode))
            if mode == "dark":
                self.assertEqual({bool(m) for m, _, _ in entries}, {True, False}, p)

    def test_every_block_declares_the_same_tokens(self):
        _, found, high = blocks()
        names = {frozenset(d) for entries in found.values() for _, _, d in entries}
        names |= {frozenset(d) for _, _, d in high}
        self.assertEqual(len(names), 1)
        self.assertIn("color-scheme", next(iter(names)))

    def test_each_palette_passes_wcag_contrast(self):
        _, found, _ = blocks()
        for p in PALETTES:
            for mode in ("light", "dark"):
                d = found[p, mode][0][2]
                self.assertEqual(d["color-scheme"], mode)
                for fg, bg, minimum in PAIRS:
                    with self.subTest(palette=p, mode=mode, pair=(fg, bg)):
                        self.assertGreaterEqual(contrast(d["--" + fg], d["--" + bg]), minimum)

    def test_the_default_without_attributes_is_catppuccin_latte_and_mocha(self):
        _, found, _ = blocks()
        html = re.search(r"<html\b[^>]*>", PAGE).group(0)
        self.assertNotIn("data-palette", html)
        self.assertNotIn("data-mode", html)
        light, dark = found[None, "light"], found[None, "dark"]
        self.assertEqual([s for _, s, _ in light], [":root"])
        self.assertEqual(sorted(s for _, s, _ in dark), [":root:not([data-mode=light])", ":root[data-mode=dark]"])
        self.assertEqual(light[0][2], found["catppuccin", "light"][0][2])
        self.assertEqual(dark[0][2], found["catppuccin", "dark"][0][2])
        latte, mocha = light[0][2], dark[0][2]
        self.assertEqual((latte["--text"], latte["--panel"], latte["--bg"]), ("#4C4F69", "#EFF1F5", "#E6E9EF"))
        self.assertEqual((mocha["--text"], mocha["--bg"], mocha["--btn"]), ("#CDD6F4", "#1E1E2E", "#CBA6F7"))

    def test_high_contrast_overrides_every_palette(self):
        _, _, high = blocks()
        self.assertTrue(high)
        for media, sel, decls in high:
            self.assertIn("forced-colors:active", media)
            for name, value in decls.items():
                self.assertTrue(value.endswith("!important"), (sel, name))
        by_mode = {}
        for media, sel, decls in high:
            dark = "[data-mode=dark]" in sel or ":not([data-mode=light])" in sel
            by_mode.setdefault("dark" if dark else "light", []).append(
                {k: v[:-len("!important")] for k, v in decls.items()})
        self.assertEqual(set(by_mode), {"light", "dark"})
        for mode, sets in by_mode.items():
            d = sets[0]
            self.assertTrue(all(s == d for s in sets), mode)
            for fg, bg, minimum in HIGH_PAIRS:
                with self.subTest(mode=mode, pair=(fg, bg)):
                    self.assertGreaterEqual(contrast(d["--" + fg], d["--" + bg]), minimum)

    def test_tokens_live_only_in_the_theme_blocks(self):
        styles, _, _ = blocks()
        for attrs, _ in styles:
            self.assertEqual(attrs, ' nonce="__NONCE__"')
        rest = PAGE.replace(theme_region(), "")
        css = "".join(re.findall(r"<style[^>]*>(.*?)</style>", rest, re.S))
        self.assertNotRegex(css, r"--[a-z0-9]+\s*:")
        # The mark in the header and the sidebar is the product icon, the one rule with fixed colours.
        css = re.sub(r"h1::before,\.logo\{.*?\}", "", css, flags=re.S)
        self.assertNotRegex(css, r"#[0-9A-Fa-f]{3,8}\b")
        self.assertNotIn("data-theme", PAGE)
        # The #light and #dark hashes set data-mode, through the same look the picker starts from.
        self.assertIn("if (theme === 'dark' || theme === 'light') look = {theme: theme, palette: look.palette};", PAGE)
        self.assertIn("root.setAttribute('data-mode', l.theme)", PAGE)

    def test_the_page_uses_every_token_and_only_these(self):
        _, found, _ = blocks()
        declared = {k[2:] for k in found["catppuccin", "light"][0][2] if k.startswith("--")}
        rest = PAGE.replace(theme_region(), "")
        self.assertEqual(set(re.findall(r"var\(--([a-z0-9]+)\)", rest)), declared)

    def test_the_credits_ship_with_the_plugin(self):
        # An install gets only plugin/, so the notices sit there too, the same text as at the root.
        root = signoff.HERE.parent.parent
        notices = (root / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8")
        self.assertEqual((root / "plugin" / "THIRD_PARTY_NOTICES").read_text(encoding="utf-8"), notices)
        for credit in ("Copyright (c) 2021 Catppuccin", "Copyright (c) mvllow", "Pavel Pertsev (morhetz)",
                       "Copyright (c) 2018-present Enkia", "Copyright (c) 2023 Dracula Theme"):
            self.assertIn(credit, notices)


if __name__ == "__main__":
    unittest.main()
