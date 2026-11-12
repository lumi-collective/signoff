#!/usr/bin/env python3
"""The page's colour themes: six palettes in light and dark, and one high-contrast set, as CSS.

    python3 -I -B tests/theme_tokens.py

prints the part of plugin/signoff/page.html between its theme markers. Paste it there by hand when a colour
changes; test_theme.py fails while the two differ, and measures every contrast pair on page.html itself.

Each palette's raw colours are the upstream values (the source and key in a comment), mapped onto the page's
tokens. A token that misses its WCAG 2.x pair is moved toward the palette's own text colour (a coloured role
toward black in light, white in dark, so an orange stays orange) in 1% steps until it passes. The CSS names
every token moved so. Not needed at run time: the page holds the output. Standard library only, Python 3.9+.
"""
import sys

PALETTE_IDS = ("catppuccin", "signoff", "rose-pine", "gruvbox", "tokyo-night", "dracula")
DEFAULT = "catppuccin"


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def hexs(c):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(v))) for v in c)


def lum(h):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix(a, b, t):
    """t of a, 1 - t of b."""
    ca, cb = rgb(a), rgb(b)
    return hexs(tuple(ca[i] * t + cb[i] * (1 - t) for i in range(3)))


def rgba(h, a):
    return "rgba(%d,%d,%d,%s)" % (rgb(h) + (("%.2f" % a).lstrip("0"),))


# ---------- raw palettes: upstream hexes only ----------

# Catppuccin Latte and Mocha: github.com/catppuccin/palette palette.json v1.8.0 (07d02aa).
LATTE = dict(red="#d20f39", peach="#fe640b", yellow="#df8e1d", green="#40a02b", sapphire="#209fb5", blue="#1e66f5",
             mauve="#8839ef", pink="#ea76cb", text="#4c4f69", subtext1="#5c5f77", subtext0="#6c6f85",
             overlay2="#7c7f93", surface1="#bcc0cc", surface0="#ccd0da", base="#eff1f5", mantle="#e6e9ef",
             crust="#dce0e8")
MOCHA = dict(red="#f38ba8", peach="#fab387", yellow="#f9e2af", green="#a6e3a1", sapphire="#74c7ec", blue="#89b4fa",
             mauve="#cba6f7", pink="#f5c2e7", text="#cdd6f4", subtext1="#bac2de", subtext0="#a6adc8",
             overlay0="#6c7086", surface1="#45475a", surface0="#313244", base="#1e1e2e", mantle="#181825")
# Rose Pine Dawn and Main: github.com/rose-pine/rose-pine-palette palette.json (92af52b); highlight_* from its
# dist/css/rose-pine.css. Dawn text is palette.json's #464261 (the older dist css still says #575279).
DAWN = dict(base="#faf4ed", surface="#fffaf3", overlay="#f2e9e1", muted="#9893a5", subtle="#797593", text="#464261",
            love="#b4637a", gold="#ea9d34", rose="#d7827e", pine="#286983", foam="#56949f", iris="#907aa9",
            hl_low="#f4ede8", hl_med="#dfdad9", hl_high="#cecacd")
MAIN = dict(base="#191724", surface="#1f1d2e", overlay="#26233a", muted="#6e6a86", subtle="#908caa", text="#e0def4",
            love="#eb6f92", gold="#f6c177", rose="#ebbcba", pine="#31748f", foam="#9ccfd8", iris="#c4a7e7",
            hl_low="#21202e", hl_med="#403d52")
# Gruvbox light and dark, medium contrast: github.com/morhetz/gruvbox colors/gruvbox.vim (ef8864b).
GL = dict(bg0_h="#f9f5d7", bg0="#fbf1c7", bg1="#ebdbb2", bg2="#d5c4a1", bg3="#bdae93", fg1="#3c3836", fg2="#504945",
          fg3="#665c54", gray="#928374", red="#9d0006", green="#79740e", yellow="#b57614", blue="#076678",
          purple="#8f3f71", aqua="#427b58", orange="#af3a03")
GD = dict(bg0_h="#1d2021", bg0="#282828", bg1="#3c3836", bg2="#504945", bg4="#7c6f64", fg1="#ebdbb2", fg2="#d5c4a1",
          fg3="#bdae93", red="#fb4934", green="#b8bb26", yellow="#fabd2f", blue="#83a598", purple="#d3869b",
          aqua="#8ec07c", orange="#fe8019")
# Tokyo Night Light and Night: github.com/tokyo-night/tokyo-night-vscode-theme themes/*.json (7c0f11e), and
# nothing else; the VS Code key of each value follows it.
TL = dict(bg="#e6e7ed",  # editor.background
          sidebar="#d6d8df",  # sideBar.background
          fg="#343b58",  # terminal.foreground
          comment="#6c6e75",  # token Comment Doc
          blue="#2959aa", cyan="#006c86", magenta="#7b43ba", red="#8c4351", yellow="#8f5e15",  # terminal.ansi*
          orange="#965027",  # token Number, Boolean, Undefined, Null
          green="#385f0d")  # token String, Symbols
TN = dict(bg="#1a1b26",  # editor.background
          bg_dark="#16161e",  # sideBar.background
          hl="#292e42",  # diffEditor.diagonalFill
          fg="#c0caf5",  # token Variables
          fg_dark="#a9b1d6",  # editor.foreground
          comment="#51597d",  # token Comment
          muted="#787c99",  # foreground
          gutter="#363b54",  # editorLineNumber.foreground
          blue="#7aa2f7", cyan="#7dcfff", magenta="#bb9af7", red="#f7768e", yellow="#e0af68",  # terminal.ansi*
          orange="#ff9e64",  # token Number, Boolean, Undefined, Null
          green="#9ece6a")  # token String, Symbols
# Alucard and Dracula: github.com/dracula/dracula-theme README (8d33e54); darker from draculatheme.com/spec.
AL = dict(bg="#fffbeb", fg="#1f1f1f", comment="#6c664b", cyan="#036a96", green="#14710a", orange="#a34d14",
          pink="#a3144d", purple="#644ac9", red="#cb3a2a", yellow="#846e15")
DR = dict(bg="#282a36", darker="#21222c", line="#44475a", fg="#f8f8f2", comment="#6272a4", cyan="#8be9fd",
          green="#50fa7b", orange="#ffb86c", pink="#ff79c6", purple="#bd93f9", red="#ff5555", yellow="#f1fa8c")

NAMES = {("catppuccin", "light"): "Catppuccin Latte", ("catppuccin", "dark"): "Catppuccin Mocha",
         ("signoff", "light"): "Signoff light", ("signoff", "dark"): "Signoff dark",
         ("rose-pine", "light"): "Rose Pine Dawn", ("rose-pine", "dark"): "Rose Pine",
         ("gruvbox", "light"): "Gruvbox light", ("gruvbox", "dark"): "Gruvbox dark",
         ("tokyo-night", "light"): "Tokyo Night Light", ("tokyo-night", "dark"): "Tokyo Night",
         ("dracula", "light"): "Alucard", ("dracula", "dark"): "Dracula"}


def palettes():
    """(id, mode) -> source tokens. hues: the six monogram hues (orange, blue, green, pink, violet, yellow)."""
    L, M, D, R = LATTE, MOCHA, DAWN, MAIN
    P = {}
    P["catppuccin", "light"] = dict(
        bg=L["mantle"], chrome=L["crust"], panel=L["base"], field=L["base"], sel=mix(L["mauve"], L["base"], .12),
        hover=mix(L["surface0"], L["base"], .35), line=L["surface0"], line2=L["surface1"], text=L["text"],
        soft=L["subtext1"], quiet=L["subtext0"], accent=L["mauve"], btn=L["mauve"], btntext=L["base"],
        btnoff=mix(L["mauve"], L["base"], .14), btnofftext=L["mauve"], focus=L["mauve"], box=L["peach"],
        boxtext=L["peach"], boxsoft=mix(L["peach"], L["base"], .12), ok=L["green"],
        oksoft=mix(L["green"], L["base"], .12), mark=L["blue"],
        hues=[L["peach"], L["blue"], L["green"], L["pink"], L["mauve"], L["yellow"]])
    P["catppuccin", "dark"] = dict(
        bg=M["base"], chrome=M["mantle"], panel=mix(M["surface0"], M["base"], .45), field=M["mantle"], sel=mix(M["mauve"], M["base"], .16),
        hover=mix(M["surface0"], M["base"], .7), line=M["surface0"], line2=M["surface1"], text=M["text"],
        soft=M["subtext1"], quiet=M["subtext0"], accent=M["mauve"], btn=M["mauve"], btntext=M["base"],
        btnoff=mix(M["mauve"], M["base"], .18), btnofftext=M["mauve"], focus=M["mauve"], box=M["peach"],
        boxtext=M["peach"], boxsoft=mix(M["peach"], M["base"], .12), ok=M["green"],
        oksoft=mix(M["green"], M["base"], .12), mark=M["sapphire"],
        hues=[M["peach"], M["blue"], M["green"], M["pink"], M["mauve"], M["yellow"]])
    # Signoff's own colours: the page's look before 0.3.
    P["signoff", "light"] = dict(
        bg="#F5F5F8", chrome="#ECEBF2", panel="#FFFFFF", field="#FAFAFC", sel="#F0EDFF", hover="#EAE9F0", line="#E2E1EA",
        line2="#CFCDDA", text="#17151F", soft="#45414F", quiet="#5F5B6B", accent="#5B47C7", btn="#5B47C7",
        btntext="#FFFFFF", btnoff="#E6E1FA", btnofftext="#4A3AA8", focus="#5B47C7", box="#E8590C", boxtext="#B5410A",
        boxsoft="#FFF1E8", ok="#15703A", oksoft="#E7F6EC", mark="#0369A1",
        mono_bg=["#FDEBD8", "#E3EEFB", "#E2F4E8", "#F6E3F1", "#ECE8FB", "#FBF3D5"],
        mono_fg=["#8A3F0B", "#1D4F91", "#1F6B3A", "#86286E", "#4A3AA8", "#735A07"])
    P["signoff", "dark"] = dict(
        bg="#0F0E13", chrome="#14131A", panel="#18161F", field="#121117", sel="#241F36", hover="#1C1A24", line="#2A2733",
        line2="#3A3646", text="#EEECF3", soft="#B9B5C6", quiet="#9894A6", accent="#B9A6FF", btn="#B9A6FF",
        btntext="#15131C", btnoff="#2A2540", btnofftext="#CFC4FF", focus="#B9A6FF", box="#FF8A57", boxtext="#FF9D70",
        boxsoft="#2C1D18", ok="#4ADE80", oksoft="#16261C", mark="#38BDF8",
        mono_bg=["#3A2516", "#18283D", "#16291D", "#33192D", "#241F36", "#332B12"],
        mono_fg=["#FFB98A", "#9CC3F5", "#8BDDA7", "#F0A6DC", "#CFC4FF", "#EBCF73"])
    P["rose-pine", "light"] = dict(
        bg=D["base"], chrome=D["overlay"], panel=D["surface"], field=D["base"], sel=mix(D["iris"], D["surface"], .14), hover=D["hl_low"],
        line=D["hl_med"], line2=D["hl_high"], text=D["text"], soft=D["subtle"], quiet=D["subtle"], accent=D["pine"],
        btn=D["pine"], btntext=D["surface"], btnoff=mix(D["pine"], D["surface"], .12), btnofftext=D["pine"],
        focus=D["iris"], box=D["gold"], boxtext=D["gold"], boxsoft=mix(D["gold"], D["surface"], .14), ok=D["pine"],
        oksoft=mix(D["pine"], D["surface"], .10), mark=D["foam"],
        hues=[D["rose"], D["pine"], D["foam"], D["love"], D["iris"], D["gold"]])
    P["rose-pine", "dark"] = dict(
        bg=R["base"], chrome=R["base"], panel=R["surface"], field=R["base"], sel=R["overlay"], hover=R["hl_low"], line=R["overlay"],
        line2=R["hl_med"], text=R["text"], soft=R["subtle"], quiet=R["subtle"], accent=R["iris"], btn=R["iris"],
        btntext=R["base"], btnoff=mix(R["iris"], R["base"], .16), btnofftext=R["iris"], focus=R["iris"], box=R["gold"],
        boxtext=R["gold"], boxsoft=mix(R["gold"], R["base"], .10), ok=R["foam"], oksoft=mix(R["pine"], R["base"], .22),
        mark=R["foam"], hues=[R["rose"], R["foam"], R["pine"], R["love"], R["iris"], R["gold"]])
    P["gruvbox", "light"] = dict(
        bg=GL["bg0"], chrome=GL["bg1"], panel=GL["bg0_h"], field=GL["bg0"], sel=mix(GL["blue"], GL["bg0_h"], .10),
        hover=mix(GL["bg1"], GL["bg0_h"], .4), line=GL["bg2"], line2=GL["bg3"], text=GL["fg1"], soft=GL["fg2"],
        quiet=GL["fg3"], accent=GL["blue"], btn=GL["blue"], btntext=GL["bg0"], btnoff=mix(GL["blue"], GL["bg0_h"], .12),
        btnofftext=GL["blue"], focus=GL["blue"], box=GL["orange"], boxtext=GL["orange"],
        boxsoft=mix(GL["orange"], GL["bg0_h"], .10), ok=GL["green"], oksoft=mix(GL["green"], GL["bg0_h"], .12),
        mark=GL["aqua"], hues=[GL["orange"], GL["blue"], GL["green"], GL["purple"], GL["aqua"], GL["yellow"]])
    P["gruvbox", "dark"] = dict(
        bg=GD["bg0"], chrome=GD["bg0_h"], panel=mix(GD["bg1"], GD["bg0"], .45), field=GD["bg0_h"], sel=GD["bg1"],
        hover=mix(GD["bg1"], GD["bg0"], .7), line=GD["bg1"], line2=GD["bg2"], text=GD["fg1"], soft=GD["fg2"],
        quiet=GD["fg3"], accent=GD["blue"], btn=GD["blue"], btntext=GD["bg0"], btnoff=mix(GD["blue"], GD["bg0"], .18),
        btnofftext=GD["blue"], focus=GD["blue"], box=GD["orange"], boxtext=GD["orange"],
        boxsoft=mix(GD["orange"], GD["bg0"], .12), ok=GD["green"], oksoft=mix(GD["green"], GD["bg0"], .12),
        mark=GD["aqua"], hues=[GD["orange"], GD["blue"], GD["green"], GD["purple"], GD["aqua"], GD["yellow"]])
    # White is the light theme's own button.foreground.
    P["tokyo-night", "light"] = dict(
        bg=TL["bg"], chrome=TL["sidebar"], panel=mix("#ffffff", TL["bg"], .45), field=TL["bg"], sel=mix(TL["blue"], TL["bg"], .12),
        hover=mix(TL["sidebar"], TL["bg"], .5), line=TL["sidebar"], line2=mix(TL["comment"], TL["bg"], .35),
        text=TL["fg"], soft=mix(TL["fg"], TL["comment"], .5), quiet=TL["comment"], accent=TL["blue"], btn=TL["blue"],
        btntext=TL["bg"], btnoff=mix(TL["blue"], TL["bg"], .14), btnofftext=TL["blue"], focus=TL["blue"],
        box=TL["orange"], boxtext=TL["orange"], boxsoft=mix(TL["orange"], TL["bg"], .10), ok=TL["green"],
        oksoft=mix(TL["green"], TL["bg"], .10), mark=TL["cyan"],
        hues=[TL["orange"], TL["blue"], TL["green"], TL["red"], TL["magenta"], TL["yellow"]])
    P["tokyo-night", "dark"] = dict(
        bg=TN["bg"], chrome=TN["bg_dark"], panel=mix(TN["hl"], TN["bg"], .4), field=TN["bg_dark"], sel=mix(TN["blue"], TN["bg"], .16),
        hover=mix(TN["hl"], TN["bg"], .6), line=TN["hl"], line2=TN["gutter"], text=TN["fg"], soft=TN["fg_dark"],
        quiet=TN["muted"], accent=TN["blue"], btn=TN["blue"], btntext=TN["bg"], btnoff=mix(TN["blue"], TN["bg"], .18),
        btnofftext=TN["blue"], focus=TN["blue"], box=TN["orange"], boxtext=TN["orange"],
        boxsoft=mix(TN["orange"], TN["bg"], .12), ok=TN["green"], oksoft=mix(TN["green"], TN["bg"], .12),
        mark=TN["cyan"], hues=[TN["orange"], TN["blue"], TN["green"], TN["red"], TN["magenta"], TN["yellow"]])
    P["dracula", "light"] = dict(
        bg=AL["bg"], chrome=mix(AL["comment"], AL["bg"], .08), panel=mix("#ffffff", AL["bg"], .55), field=AL["bg"], sel=mix(AL["purple"], AL["bg"], .10),
        hover=mix(AL["comment"], AL["bg"], .05), line=mix(AL["comment"], AL["bg"], .18),
        line2=mix(AL["comment"], AL["bg"], .32), text=AL["fg"], soft=mix(AL["fg"], AL["comment"], .5),
        quiet=AL["comment"], accent=AL["purple"], btn=AL["purple"], btntext=AL["bg"],
        btnoff=mix(AL["purple"], AL["bg"], .12), btnofftext=AL["purple"], focus=AL["purple"], box=AL["orange"],
        boxtext=AL["orange"], boxsoft=mix(AL["orange"], AL["bg"], .10), ok=AL["green"],
        oksoft=mix(AL["green"], AL["bg"], .10), mark=AL["cyan"],
        hues=[AL["orange"], AL["cyan"], AL["green"], AL["pink"], AL["purple"], AL["yellow"]])
    P["dracula", "dark"] = dict(
        bg=DR["bg"], chrome=DR["darker"], panel=mix(DR["line"], DR["bg"], .3), field=DR["darker"], sel=mix(DR["purple"], DR["bg"], .16),
        hover=mix(DR["line"], DR["bg"], .5), line=mix(DR["line"], DR["bg"], .7), line2=DR["line"], text=DR["fg"],
        soft=mix(DR["fg"], DR["bg"], .8), quiet=mix(DR["fg"], DR["bg"], .65), accent=DR["purple"], btn=DR["purple"],
        btntext=DR["bg"], btnoff=mix(DR["purple"], DR["bg"], .18), btnofftext=DR["purple"], focus=DR["purple"],
        box=DR["orange"], boxtext=DR["orange"], boxsoft=mix(DR["orange"], DR["bg"], .12), ok=DR["green"],
        oksoft=mix(DR["green"], DR["bg"], .10), mark=DR["cyan"],
        hues=[DR["orange"], DR["cyan"], DR["green"], DR["pink"], DR["purple"], DR["yellow"]])
    return P


# One set for prefers-contrast: more and forced-colors, over every palette: black on white, white on black.
HIGH_CONTRAST = {
    "light": dict(
        bg="#FFFFFF", chrome="#FFFFFF", panel="#FFFFFF", field="#FFFFFF", sel="#ECE6FF", hover="#EDEDED", line="#6B6B6B",
        line2="#000000", text="#000000", soft="#1A1A1A", quiet="#2E2E2E", accent="#3F1DB0", btn="#3F1DB0",
        btntext="#FFFFFF", btnoff="#ECE6FF", btnofftext="#2E1585", focus="#000000", box="#B33A00", boxtext="#8A2C00",
        boxsoft="#FFF0E6", ok="#0A5A26", oksoft="#E6F4EA", mark="#004E89",
        mono_bg=["#FFE7D6", "#E1ECFB", "#DFF3E6", "#F7E1F1", "#ECE6FF", "#FBF1CF"],
        mono_fg=["#6B2400", "#0B3570", "#0A4A22", "#6A1556", "#2E1585", "#4F3A00"]),
    "dark": dict(
        bg="#000000", chrome="#000000", panel="#000000", field="#000000", sel="#2B2147", hover="#1C1C1C", line="#9E9E9E",
        line2="#FFFFFF", text="#FFFFFF", soft="#F2F2F2", quiet="#DEDEDE", accent="#D2C6FF", btn="#D2C6FF",
        btntext="#000000", btnoff="#2B2147", btnofftext="#E2DAFF", focus="#FFFFFF", box="#FF9F6B", boxtext="#FFC2A3",
        boxsoft="#2B1408", ok="#7BEFA4", oksoft="#0B2414", mark="#7DD3FF",
        mono_bg=["#3A1E0B", "#0F2340", "#0C2918", "#35102C", "#2B2147", "#332A08"],
        mono_fg=["#FFD0B0", "#BCD8FF", "#B1F0C6", "#FFC8EE", "#E2DAFF", "#F7E3A0"]),
}

HUE_TOKENS = ("accent", "btnofftext", "boxtext", "ok", "box", "focus", "mark")  # and m0fg..m5fg
# (token, surface, minimum): the pairs the page puts together. test_theme.py keeps its own copy of this list.
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
HC_TEXT = 7.0  # the high-contrast set meets AAA for text, and 3:1 for its plain lines too


def hc_pairs():
    return ([(t, s, HC_TEXT if m == 4.5 else m) for t, s, m in PAIRS] + [("line", s, 3.0) for s in ("bg", "panel")])


def settle(src, mode, pairs):
    """Move each failing token until its pair passes; return the tokens and the names of those moved."""
    m = {k: v for k, v in src.items() if not isinstance(v, list)}
    hues = src.get("hues")
    for i in range(6):
        m["m%dbg" % i] = src["mono_bg"][i] if "mono_bg" in src else mix(hues[i], m["panel"], .16 if mode == "light" else .2)
        m["m%dfg" % i] = src["mono_fg"][i] if "mono_fg" in src else hues[i]
    moved = []
    for tok, against, minimum in pairs:
        if ratio(m[tok], m[against]) >= minimum:
            continue
        toward = m["text"]
        if tok in HUE_TOKENS or tok.endswith("fg"):  # the monogram letters are coloured too
            toward = "#000000" if mode == "light" else "#FFFFFF"
        if tok == "btntext":
            toward = "#FFFFFF" if lum(m["btn"]) < 0.18 else "#000000"
        for step in range(1, 101):
            new = mix(toward, m[tok], step / 100)
            if ratio(new, m[against]) >= minimum + 0.05:  # a margin, so rounding never lands on the line
                m[tok] = new
                break
        else:
            raise SystemExit("cannot reach %s for %s on %s" % (minimum, tok, against))
        if tok not in moved:
            moved.append(tok)
    return m, moved


def finish(m, mode, hc=False):
    """The tokens that are not plain colours, in the order they are written."""
    m = {k: (v.upper() if v.startswith("#") else v) for k, v in m.items()}
    light = mode == "light"
    m["bgglass"] = rgba(m["bg"], .9 if light else .88)  # the sticky header; text sits on it
    m["glassline"] = rgba(m["text"], .10 if light else .12)
    m["dim"] = rgba(m["text"] if light else "#000000", .42 if light else .5)
    m["markfill"] = rgba(m["mark"], .14 if light else .18)
    m["shadow"] = ("0 1px 2px rgba(23,21,31,.06),0 1px 1px rgba(23,21,31,.04)" if light
                   else "0 0 0 1px rgba(255,255,255,.02)")
    m["shadow2"] = "0 1px 3px rgba(23,21,31,.12)" if light else "0 1px 3px rgba(0,0,0,.5)"
    if hc:
        m["shadow"] = m["shadow2"] = "none"
    return m


ORDER = ("bg chrome panel field sel hover line line2 text soft quiet accent btn btntext btnoff btnofftext "
         "focus box boxtext boxsoft ok oksoft mark markfill "
         + " ".join("m%dbg m%dfg" % (i, i) for i in range(6))
         + " bgglass glassline dim shadow shadow2").split()


def decls(m, mode, important=False):
    bang = "!important" if important else ""
    return ";".join(["color-scheme:%s%s" % (mode, bang)] + ["--%s:%s%s" % (k, m[k], bang) for k in ORDER])


def build():
    """(id, mode) -> (tokens, moved) for every palette, and the high-contrast set by mode."""
    out = {}
    for key, src in palettes().items():
        m, moved = settle(src, key[1], PAIRS)
        out[key] = (finish(m, key[1]), moved)
    hc = {}
    for mode, src in HIGH_CONTRAST.items():
        m, moved = settle(src, mode, hc_pairs())
        hc[mode] = (finish(m, mode, hc=True), moved)
    return out, hc


def css():
    out, hc = build()
    parts = ["<!-- theme: generated by tests/theme_tokens.py; test_theme.py fails when this differs -->"]
    for pid in PALETTE_IDS:
        for mode in ("light", "dark"):
            m, moved = out[pid, mode]
            note = "%s. Moved for contrast: %s." % (NAMES[pid, mode], " ".join("--" + t for t in moved) or "none")
            d = decls(m, mode)
            # Not only :root: the picker's swatches carry data-palette and data-mode to show a palette in its colours.
            if mode == "light":
                sel = ([":root"] if pid == DEFAULT else []) + ["[data-palette=%s]" % pid]
                rules = "%s{%s}" % (",".join(sel), d)
            else:
                sel = ([":root[data-mode=dark]"] if pid == DEFAULT else []) + ["[data-palette=%s][data-mode=dark]" % pid]
                auto = ([":root:not([data-mode=light])"] if pid == DEFAULT else []) \
                    + ["[data-palette=%s]:not([data-mode=light])" % pid]
                rules = "%s{%s}\n@media (prefers-color-scheme:dark){%s{%s}}" % (",".join(sel), d, ",".join(auto), d)
            parts.append('<style nonce="__NONCE__">\n/* %s */\n%s\n</style>' % (note, rules))
    light, dark = (decls(hc[mode][0], mode, important=True) for mode in ("light", "dark"))
    moved = sorted(set(hc["light"][1] + hc["dark"][1]))
    parts.append('<style nonce="__NONCE__">\n/* High contrast, over every palette. Moved for contrast: %s. */\n'
                 "@media (prefers-contrast:more),(forced-colors:active){:root{%s}:root[data-mode=dark]{%s}}\n"
                 "@media (prefers-contrast:more) and (prefers-color-scheme:dark),"
                 "(forced-colors:active) and (prefers-color-scheme:dark){:root:not([data-mode=light]){%s}}\n</style>"
                 % (" ".join("--" + t for t in moved) or "none", light, dark, dark))
    parts.append("<!-- /theme -->")
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    sys.stdout.write(css())
