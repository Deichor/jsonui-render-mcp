"""Draws a Bedrock JSON UI screen from a pack dump, the way the client would.

Anything it does not understand is reported, not guessed at. A device is still ground truth.
"""

import json
import os
import re
import sys
from pathlib import Path
from PIL import Image

from .templates import Index, truth

SCALE = 2
FONT_DIR = Path(os.environ.get("MINECRAFT_FONT", Path.home() / ".aseprite-mcp/fonts/minecraft"))
SAMPLES = Path(os.environ.get("BEDROCK_SAMPLES", Path.home() / ".cache/bedrock-samples"))
VANILLA_UI = SAMPLES / "resource_pack/ui"


# --- font ---------------------------------------------------------------------------------------

# A formatting code and the character it governs. The client reads these as instructions and draws
# none of them, so anything measuring or drawing a caption has to take them off first — a marker is
# thirteen of these on the front of a line, and left in they are thirteen visible letters and about
# eighty pixels of width that is not there.
FORMATTING = re.compile("\u00a7.")


def plain(text):
    """A caption as the characters the client actually draws."""
    return FORMATTING.sub("", text) if isinstance(text, str) else text


class Font:
    """Minecraft's bitmap font, measured the way the game measures it: by trimming blank columns."""

    def __init__(self, directory):
        meta = json.loads((directory / "font.json").read_text())
        self.letter_gap = meta["letter_gap"]
        self.space_width = meta["space_width"]
        self.glyphs = {}
        # Everything hangs from the ASCII sheet's baseline; taller sheets reach above it.
        self.baseline = meta["sheets"][0]["ascent"]
        for sheet in meta["sheets"]:
            image = Image.open(directory / sheet["file"]).convert("RGBA")
            cw, ch = sheet["cell_w"], sheet["cell_h"]
            ascent = sheet["ascent"]
            for row, chars in enumerate(sheet["chars"]):
                for col, ch_char in enumerate(chars):
                    if ch_char == "\x00":
                        continue
                    cell = image.crop((col * cw, row * ch, col * cw + cw, row * ch + ch))
                    self.glyphs.setdefault(ch_char, (cell, self._width(cell), ascent))

    @staticmethod
    def _width(cell):
        # A glyph is as wide as its ink, not as its cell.
        alpha = cell.split()[3]
        box = alpha.getbbox()
        return (box[2] if box else 0)

    def measure(self, text):
        width = 0
        for ch in plain(text):
            if ch == " ":
                width += self.space_width + self.letter_gap
            elif ch in self.glyphs:
                width += self.glyphs[ch][1] + self.letter_gap
            else:
                width += self.space_width + self.letter_gap
        return max(0, width - self.letter_gap)

    def wrap(self, text, width):
        """Breaks at a space, and inside a word only if it cannot fit. Labels wrap, never truncate."""
        # A break the text asks for comes first, and is not negotiable: a client honours it whether
        # or not the line would have fit. Wrapping each piece afterwards is what a long one gets.
        if "\n" in text:
            return [line for piece in text.split("\n") for line in self.wrap(piece, width)]
        if not width or width <= 0 or self.measure(text) <= width:
            return [text]
        lines, line = [], ""
        for word in text.split(" "):
            candidate = f"{line} {word}" if line else word
            if self.measure(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            while self.measure(word) > width:
                cut = len(word)
                while cut > 1 and self.measure(word[:cut]) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        if line:
            lines.append(line)
        return lines

    def draw(self, target, text, x, y, colour, shadow=False):
        """Draws at (x, y) as the top-left of the *cell*, which is what a label's box means."""
        if shadow:
            self.draw(target, text, x + 1, y + 1, (63, 63, 63, 255))
        cursor = x
        for ch in plain(text):
            if ch == " ":
                cursor += self.space_width + self.letter_gap
                continue
            glyph = self.glyphs.get(ch)
            if glyph is None:
                cursor += self.space_width + self.letter_gap
                continue
            cell, width, ascent = glyph
            tinted = Image.new("RGBA", cell.size, colour)
            tinted.putalpha(cell.split()[3])
            # Baseline-aligned, so an accent reaches above the line instead of below it.
            target.alpha_composite(tinted, (cursor, y + self.baseline - ascent))
            cursor += width + self.letter_gap


# --- textures -----------------------------------------------------------------------------------

class Textures:
    def __init__(self, directory, vanilla=None):
        self.directory = directory
        self.vanilla = vanilla
        self.slices = json.loads((directory / "nineslice.json").read_text())
        self.cache = {}
        self.missing = set()

    def get(self, path):
        name = path.rsplit("/", 1)[-1]
        if path not in self.cache:
            # A dump is flat; Mojang's keep their path, which separates items/ from blocks/.
            file = self.directory / f"{name}.png"
            if not file.exists() and self.vanilla:
                for candidate in (self.vanilla / f"{path}.png", self.vanilla / "textures/ui" / f"{name}.png"):
                    if candidate.exists():
                        file = candidate
                        break
            if not file.exists():
                self.missing.add(path)
                return None, None
            self.cache[path] = Image.open(file).convert("RGBA")
            # A nine-slice border sits beside the art as a .json.
            sidecar = file.with_suffix(".json")
            if name not in self.slices and sidecar.exists():
                try:
                    meta = json.loads(sidecar.read_text())
                    self.slices[name] = meta.get("nineslice_size")
                except Exception:
                    pass
        return self.cache[path], self.slices.get(name)

    def stretched(self, path, w, h):
        """Nine-slices when the pack asked for it; a plain stretch smears a bevel into a gradient."""
        image, border = self.get(path)
        if image is None or w <= 0 or h <= 0:
            return None
        if not border:
            return image.resize((w, h), Image.NEAREST)
        # Mojang writes a uniform border as one number and ours as four.
        if isinstance(border, (int, float)):
            border = [int(border)] * 4
        if len(border) != 4:
            return image.resize((w, h), Image.NEAREST)
        left, top, right, bottom = border
        iw, ih = image.size
        out = Image.new("RGBA", (w, h))

        def piece(box, dest_box):
            part = image.crop(box)
            dw, dh = dest_box[2] - dest_box[0], dest_box[3] - dest_box[1]
            if dw <= 0 or dh <= 0:
                return
            out.paste(part.resize((dw, dh), Image.NEAREST), dest_box[:2])

        mid_w, mid_h = max(0, w - left - right), max(0, h - top - bottom)
        piece((0, 0, left, top), (0, 0, left, top))
        piece((iw - right, 0, iw, top), (w - right, 0, w, top))
        piece((0, ih - bottom, left, ih), (0, h - bottom, left, h))
        piece((iw - right, ih - bottom, iw, ih), (w - right, h - bottom, w, h))
        piece((left, 0, iw - right, top), (left, 0, left + mid_w, top))
        piece((left, ih - bottom, iw - right, ih), (left, h - bottom, left + mid_w, h))
        piece((0, top, left, ih - bottom), (0, top, left, top + mid_h))
        piece((iw - right, top, iw, ih - bottom), (w - right, top, w, top + mid_h))
        piece((left, top, iw - right, ih - bottom), (left, top, left + mid_w, top + mid_h))
        return out


# --- layout -------------------------------------------------------------------------------------

PROPERTY = re.compile(r"#[\w.]+")

# What one line of the default font costs, and what a label's `default` height is a multiple of.
LINE_HEIGHT = 10

ANCHORS = {
    "top_left": (0.0, 0.0), "top_middle": (0.5, 0.0), "top_right": (1.0, 0.0),
    "left_middle": (0.0, 0.5), "center": (0.5, 0.5), "right_middle": (1.0, 0.5),
    "bottom_left": (0.0, 1.0), "bottom_middle": (0.5, 1.0), "bottom_right": (1.0, 1.0),
}


TERM = re.compile(r"([+-]?)\s*([0-9.]+)\s*(%cm|%c|%|px)?")


def axis(value, parent, content, font, text, content_max=None, problems=None):
    """One axis of a size: a sum of parent (`%`), content (`%c`, `%cm`) and pixel terms."""
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return 0
    if value == "default":
        return font.measure(text) if text is not None else content
    # `fill` and a variable no ancestor set both used to come out nought, and a nought-sized box is
    # not a small box — it is a `clips_children` window that clips to nothing, so the renderer gave
    # up on clipping and drew the list straight through the rule and the foot beneath it. The
    # parent's own size is the honest reading of both: `fill` means exactly that, and a scroll
    # port's stack is bounded by the port whatever names it.
    if value == "fill" or value.startswith("$"):
        if problems is not None:
            problems.append(f"size '{value}' is not modelled here; taken as the parent's {parent}px")
        return int(parent)

    total, seen = 0.0, False
    for sign, number, unit in TERM.findall(value):
        amount = float(number)
        if sign == "-":
            amount = -amount
        if unit == "%":
            total += parent * amount / 100
        elif unit == "%c":
            total += content * amount / 100
        elif unit == "%cm":
            total += (content if content_max is None else content_max) * amount / 100
        else:
            total += amount
        seen = True
    return int(total) if seen else 0


class Renderer:
    def __init__(self, controls, textures, font, entries, title, problems, index, icons=None):
        self.index = index
        self.ops = []
        # Set here as well as in `draw`, so a caller that lays out without painting — `check_anchors`
        # does — does not fall over on the first sibling it has to order.
        self.layers = {}
        self.controls = controls
        self.textures = textures
        self.font = font
        self.entries = entries
        self.icons = icons or {}
        self.title = title
        self.problems = problems

    def entry(self, index):
        if index is None:
            return None
        if index >= len(self.entries):
            self.problems.append(f"entry {index} is read but the sender only sends {len(self.entries)}")
            return ""
        return self.entries[index]

    def icon(self, index):
        """The picture the form sent for an entry, if there is one."""
        if index is None:
            return ""
        return self.icons.get(str(index), "")

    def properties(self, control, index):
        """The `#properties` a control's bindings define. A form sends a caption and a texture."""
        scope = {}
        for binding in control.get("bindings", []) or []:
            if binding.get("binding_type") == "view":
                continue
            source = binding.get("binding_name")
            name = binding.get("binding_name_override") or source
            if not name:
                continue
            if source == "#form_button_text":
                scope[name] = self.entry(index) or ""
            elif source == "#form_button_texture":
                scope[name] = self.icon(index)
            elif source == "#form_button_texture_file_system":
                scope[name] = ""
        return scope

    def hidden(self, control, index):
        """Whether a `#visible` binding switches this off. Unknown properties are left visible.

        **The last binding wins.** Two of them writing `#visible` do not combine into an `and`: the
        client evaluates each in turn and the one that runs last is the one that stands. Reading
        them as an `and` here hid a control the client draws, which is the direction that lies —
        a pack whose gates contradict each other looked correct in a render and wrong on a phone.
        """
        scope = self.properties(control, index)
        visible = True
        for binding in control.get("bindings", []) or []:
            source = binding.get("source_property_name")
            if binding.get("target_property_name") != "#visible" or not source:
                continue
            unknown = [name for name in PROPERTY.findall(source) if name not in scope]
            if unknown:
                self.problems.append(f"binding reads {', '.join(sorted(set(unknown)))}, which this cannot resolve; drawn anyway")
                continue
            visible = bool(truth(source, scope))
        return not visible

    def text_of(self, control, index):
        raw = control.get("text")
        if raw in ("#null", None) and control.get("type") == "label":
            return None
        if raw == "#title_text":
            return self.title
        if raw is not None and index is not None:
            return self.entry(index) or ""
        return None

    def tint(self, colour, fallback=(0, 0, 0, 255)):
        """A colour as pixels. Anything that is not three numbers is reported, not guessed."""
        if isinstance(colour, (list, tuple)) and len(colour) >= 3:
            try:
                return tuple(int(float(c) * 255) for c in colour[:3]) + (255,)
            except (TypeError, ValueError):
                pass
        if colour is not None:
            self.problems.append(f"colour '{str(colour)[:40]}' is not three numbers; drawn as default")
        return fallback

    @staticmethod
    def multiply(art, tint):
        r, g, b, a = art.split()
        scale = [tint[0] / 255, tint[1] / 255, tint[2] / 255]
        r = r.point(lambda v: int(v * scale[0]))
        g = g.point(lambda v: int(v * scale[1]))
        b = b.point(lambda v: int(v * scale[2]))
        return Image.merge("RGBA", (r, g, b, a))

    @staticmethod
    def other_states(control):
        """The state children a button names but does not show at rest."""
        resting = control.get("default_control")
        named = {control.get(key) for key in ("hover_control", "pressed_control", "locked_control")}
        return {name for name in named if name} - {resting}

    @staticmethod
    def bound(control, w, h, parent_w, parent_h, font):
        """A label's box after `max_size`, which is what decides where the text breaks."""
        limit = control.get("max_size")
        if not isinstance(limit, list) or len(limit) < 2:
            return w, h
        bw = axis(limit[0], parent_w, w, font, None) or w
        bh = axis(limit[1], parent_h, h, font, None) or h
        return (min(w, bw) if w else bw), (min(h, bh) if h else bh)

    def lines_of(self, control, text, w, h, parent_w, parent_h):
        """The lines a label shows, and the ones its box leaves no room for."""
        bw, bh = self.bound(control, w, h, parent_w, parent_h, self.font)
        lines = self.font.wrap(text, bw)
        room = max(1, bh // LINE_HEIGHT) if bh else len(lines)
        return lines[:room], lines[room:]

    def children(self, control):
        out = []
        skip = self.other_states(control)
        for entry in control.get("controls", []) or []:
            for name, child in entry.items():
                if not isinstance(child, dict):
                    continue
                # Names arrive flattened: the index expanded `name@ref` while its scope existed.
                if name in skip:
                    continue
                out.append((name, child))
        return out

    def measure(self, control, parent_w, parent_h, index):
        """Resolves a control's own box, recursing where a size is expressed as its content."""
        index = control.get("collection_index", index)
        size = control.get("size", ["100%", "100%"])
        text = self.text_of(control, index)

        content_w = content_h = max_w = max_h = 0
        kids = self.children(control)
        needs_content = any(isinstance(v, str) and ("%c" in v) for v in size)
        if needs_content and kids:
            stack = control.get("type") == "stack_panel"
            vertical = control.get("orientation") == "vertical"
            for _, child in kids:
                cw, ch = self.measure(child, parent_w, parent_h, index)
                max_w, max_h = max(max_w, cw), max(max_h, ch)
                if stack and vertical:
                    content_h += ch
                    content_w = max(content_w, cw)
                elif stack:
                    content_w += cw
                    content_h = max(content_h, ch)
                else:
                    content_w, content_h = max(content_w, cw), max(content_h, ch)

        w = axis(size[0], parent_w, content_w, self.font, text, max_w, self.problems)
        h = axis(size[1], parent_h, content_h, self.font, text, max_h, self.problems)
        if text is not None and size[1] == "default":
            h = 8
        return w, h

    def layout(self, control, x, y, parent_w, parent_h, index=None, clip=None, frame=None):
        """Places a control and everything under it, without painting anything yet.

        `layer` orders a sibling group and nothing wider — but a sibling is a *subtree*, and it is
        ordered by the deepest layer anywhere inside it, not by the number on its own root. A plate
        is the case: its recess carries 20 and the panel holding its figure carries nothing at all,
        so by their own numbers the recess draws last and paints over the figure it is behind. On a
        phone the figure is there. Taking the subtree's own maximum puts it back without reaching
        for a screen-wide depth, which is the thing that does contradict a device.
        """
        index = control.get("collection_index", index)
        if self.hidden(control, index):
            return 0, 0

        w, h = self.measure(control, parent_w, parent_h, index)
        offset = control.get("offset", [0, 0])
        anchor_from = ANCHORS.get(control.get("anchor_from", "top_left"), (0.0, 0.0))
        anchor_to = ANCHORS.get(control.get("anchor_to", "top_left"), (0.0, 0.0))

        # The child's anchor_to point lands on the parent's anchor_from point, then offsets.
        ox = axis(offset[0], parent_w, 0, self.font, None)
        oy = axis(offset[1], parent_h, 0, self.font, None)
        px = x + int(parent_w * anchor_from[0]) - int(w * anchor_to[0]) + ox
        py = y + int(parent_h * anchor_from[1]) - int(h * anchor_to[1]) + oy

        self.ops.append((control, px, py, w, h, index, clip))

        # `clips_children` is Mojang's own, and it is what a scroll view port is: the content is
        # laid out at its full height and the window shows a slice. Without it a grid of 36 cards
        # draws straight through the rule and the foot beneath it, which reads as a broken layout
        # rather than as the renderer declining to scroll.
        if control.get("clips_children"):
            box = (px, py, px + w, py + h)
            if box[2] <= box[0] or box[3] <= box[1]:
                # A window this never measured — `fill` and the scrolling panel's own internals are
                # not modelled here, and they come out nought by nought. Clipping to nothing would
                # erase everything inside it, so the box falls back to the parent's: a scroll port
                # is bounded by whatever holds it, and a window that fills its parent is the right
                # answer far more often than no window at all. Drawing it unclipped instead let a
                # list run straight through the rule and the foot under it, which reads as a broken
                # screen rather than as the renderer declining to scroll.
                # The parent first, then the nearest ancestor that was measured at all: a scroll
                # port's own internals collapse in a chain, so the one directly above is often nought
                # as well and only [frame] still names a real box.
                box = (x, y, x + parent_w, y + parent_h)
                if box[2] <= box[0] or box[3] <= box[1]:
                    box = frame or box
                self.problems.append(
                    f"a clips_children window measured {w}x{h}; clipped to {box} instead"
                )

            if box[2] > box[0] and box[3] > box[1]:
                clip = box if clip is None else (
                    max(clip[0], box[0]), max(clip[1], box[1]),
                    min(clip[2], box[2]), min(clip[3], box[3]),
                )

        # The last box that had a size, for a `clips_children` window whose own chain collapsed.
        if w > 0 and h > 0:
            frame = (px, py, px + w, py + h)

        kids = self.children(control)
        stack = control.get("type") == "stack_panel"
        vertical = control.get("orientation") == "vertical"
        # Declaration order places a stack's children; `layer` only decides what covers what.
        order = kids if stack else sorted(kids, key=lambda kv: self.deepest_layer(kv[1]))
        cursor = 0
        for _, child in order:
            if stack:
                cw, ch = self.layout(
                    child,
                    px + (0 if vertical else cursor), py + (cursor if vertical else 0),
                    w, h if not vertical else 0, index, clip, frame,
                )
                cursor += ch if vertical else cw
            else:
                self.layout(child, px, py, w, h, index, clip, frame)
        return w, h

    def deepest_layer(self, control):
        """The highest `layer` anywhere in [control]'s subtree, which is what orders it."""
        cached = self.layers.get(id(control))
        if cached is not None:
            return cached
        deepest = control.get("layer", 0)
        for _, child in self.children(control):
            deepest = max(deepest, self.deepest_layer(child))
        self.layers[id(control)] = deepest
        return deepest

    def paint(self, target):
        """Draws what layout placed, in the order layout placed it."""
        for control, px, py, w, h, index, clip in self.ops:
            if clip is not None:
                if clip[2] <= clip[0] or clip[3] <= clip[1]:
                    continue
                if not (clip[0] <= px and clip[1] <= py and px + w <= clip[2] and py + h <= clip[3]):
                    # Drawn apart and composited back through the window, which is the only way to
                    # cut a glyph or a stretched texture in half.
                    scratch = Image.new("RGBA", target.size, (0, 0, 0, 0))
                    self.paint_one(scratch, control, px, py, w, h, index)
                    target.alpha_composite(scratch.crop(clip), (clip[0], clip[1]))
                    continue
            self.paint_one(target, control, px, py, w, h, index)

    def paint_one(self, target, control, px, py, w, h, index):
        kind = control.get("type")
        if kind == "image":
            texture = control.get("texture") or self.properties(control, index).get("#texture")
            if not texture:
                return
            art = self.textures.stretched(texture, w, h)
            if art is None:
                return
            colour = control.get("color")
            if colour is not None:
                # A colour multiplies a texture. [1,1,1] is the identity, not white.
                art = self.multiply(art, self.tint(colour, (255, 255, 255, 255)))
            target.alpha_composite(art, (px, py))
        elif kind == "label":
            text = self.text_of(control, index)
            if not text:
                return
            tint = self.tint(control.get("color"))
            align = control.get("text_alignment", "left")
            shown, dropped = self.lines_of(control, text, w, h, w, h)
            if dropped:
                self.problems.append(
                    f"'{text}' does not fit its box; the client shows '{' '.join(shown)}'"
                )
            for row, line in enumerate(shown):
                tw = self.font.measure(line)
                tx = px + (w - tw) // 2 if align == "center" else (px + w - tw if align == "right" else px)
                self.font.draw(target, line, tx, py + row * LINE_HEIGHT, tint, shadow=bool(control.get("shadow")))

    def draw(self, target, control, x, y, parent_w, parent_h):
        self.ops = []
        self.layers = {}
        self.layout(control, x, y, parent_w, parent_h)
        self.paint(target)


def namespace_of(dump):
    """The namespace a dump's own controls are filed under.

    Read from the dump rather than hardcoded: this started life reading one plugin's pack, and a
    control naming another always carries a namespace, so a dump from anywhere else resolves
    nothing and reports every reference as undefined. `auction` stays the default so older dumps,
    which carry no namespace, still read.
    """
    from pathlib import Path
    import json as _json
    marker = Path(dump) / "namespace"
    if marker.exists():
        return marker.read_text().strip()
    screens = Path(dump) / "screens.json"
    if screens.exists():
        for entry in _json.loads(screens.read_text()):
            if entry.get("namespace"):
                return entry["namespace"]
    return "auction"


def render(dump, screen_name, entries, title, out_file, icons=None):
    dump = Path(dump)
    controls = json.loads((dump / "controls.json").read_text())
    screens = {s["name"]: s for s in json.loads((dump / "screens.json").read_text())}
    screen = screens[screen_name]

    font = Font(FONT_DIR)
    textures = Textures(dump / "textures", VANILLA_UI.parent)
    problems = []
    namespace = namespace_of(dump)
    index = Index(VANILLA_UI, namespace, controls, problems)
    renderer = Renderer(controls, textures, font, entries, title, problems, index, icons)

    w, h = screen["width"], screen["height"]
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    # A variant of Mojang's own dialog, so the template decides where the body sits.
    variant = {
        "size": [w, h],
        "$text_name": "#title_text",
        "$title_text_binding_type": "none",
        "$child_control": f"{namespace}.{screen['body']}",
        "layer": 2,
    }
    if screen.get("background"):
        variant["$custom_background"] = f"{namespace}.{screen['background']}"
    if screen.get("title"):
        variant["$use_custom_title_control"] = True
        variant["$custom_title_label"] = f"{namespace}.{screen['title']}"
    variant["$title_size"] = ["100% - 16px", 16]
    variant["$title_offset"] = [0, 3]

    root = index.resolve("common_dialogs.main_panel_no_buttons", variant)
    if root is None:
        problems.append("the dialog itself resolved to a control the client would not create")
        root = {}
    renderer.draw(canvas, root, 0, 0, w, h)

    # A missing texture draws as nothing, which reads as an empty panel. Say so instead.
    problems.extend(
        f"texture '{name}' is in no pack this reads; drawn as nothing" for name in sorted(textures.missing)
    )

    canvas.resize((w * SCALE, h * SCALE), Image.NEAREST).save(out_file)
    return problems


def render_control(dump, ref, width, height, out_file, entries=None, title=""):
    """Draws any control by name, e.g. one of Mojang's, to find what this does not implement."""
    dump = Path(dump)
    controls = json.loads((dump / "controls.json").read_text())
    font = Font(FONT_DIR)
    textures = Textures(dump / "textures", VANILLA_UI.parent)
    problems = []
    index = Index(VANILLA_UI, namespace_of(dump), controls, problems)
    renderer = Renderer(controls, textures, font, entries or [], title, problems, index)

    root = index.resolve(ref, {"size": [width, height]})
    if root is None:
        problems.append(f"'{ref}' resolved to a control the client would not create")
        root = {}
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    renderer.draw(canvas, root, 0, 0, width, height)
    problems.extend(
        f"texture '{name}' is in no pack this reads; drawn as nothing" for name in sorted(textures.missing)
    )
    canvas.resize((width * SCALE, height * SCALE), Image.NEAREST).save(out_file)
    return problems
