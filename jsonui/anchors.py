"""Checks the anchor rule against Mojang's own dialogs."""

import json
import sys
from pathlib import Path

from . import render as R
from .templates import Index

# Real geometry from Mojang's dialogs, including the 344x201 body a vertical budget rests on.
CASES = [
    (
        "common_dialogs.main_panel_two_buttons",
        {"$top_button_panel": "common.empty_panel", "$bottom_button_panel": "common.empty_panel"},
        {
            "panel_indent": (8, 23, 344, 133),
            "top_button_panel": (7, 163, 346, 30),
            "bottom_button_panel": (7, 195, 346, 30),
        },
    ),
    (
        "common_dialogs.main_panel_no_buttons",
        {},
        {"panel_indent": (8, 23, 344, 201)},
    ),
]

# The dialogs anchor both ends the same, so a swap leaves them untouched. These catch it.
ROLES = [
    # from center (180,116), minus right_middle of a 100x50 box (100,25), minus 1
    ("center", "right_middle", [100, 50], [-1, 0], (79, 91)),
    # from top_left (0,0), minus bottom_left of a 146x16 box (0,16), minus 5: hung above the parent
    ("top_left", "bottom_left", [146, 16], [0, -5], (0, -21)),
]


def rects(dump, ref, overrides):
    controls = json.loads((Path(dump) / "controls.json").read_text())
    problems = []
    index = Index(R.VANILLA_UI, R.namespace_of(dump), controls, problems)
    renderer = R.Renderer(
        controls,
        R.Textures(Path(dump) / "textures", R.VANILLA_UI.parent),
        R.Font(R.FONT_DIR),
        [], "", problems, index,
    )
    root = index.resolve(ref, {"size": [360, 232], "$child_control": "common.empty_panel", **overrides})
    renderer.ops = []
    renderer.layout(root, 0, 0, 360, 232)

    named = {}

    def walk(control):
        for entry in control.get("controls", []) or []:
            for name, child in entry.items():
                named[id(child)] = name
                walk(child)

    walk(root)
    found = {}
    for control, x, y, w, h, _ in renderer.ops:
        name = named.get(id(control))
        if name:
            found.setdefault(name, (x, y, w, h))
    return found, problems


def main(dump):
    failures = []
    for anchor_from, anchor_to, size, offset, want in ROLES:
        controls = json.loads((Path(dump) / "controls.json").read_text())
        problems = []
        index = Index(R.VANILLA_UI, R.namespace_of(dump), controls, problems)
        renderer = R.Renderer(
            controls,
            R.Textures(Path(dump) / "textures", R.VANILLA_UI.parent),
            R.Font(R.FONT_DIR), [], "", problems, index,
        )
        root = index.resolve(
            "common.empty_panel",
            {"size": size, "offset": offset, "anchor_from": anchor_from, "anchor_to": anchor_to},
        )
        renderer.ops = []
        renderer.layout(root, 0, 0, 360, 232)
        got = renderer.ops[0][1:3]
        if got != want:
            failures.append(f"{anchor_from} -> {anchor_to}: expected {want}, drew {got}")

    for ref, overrides, expected in CASES:
        found, problems = rects(dump, ref, overrides)
        failures += [f"{ref}: {problem}" for problem in problems]
        for name, want in expected.items():
            got = found.get(name)
            if got != want:
                failures.append(f"{ref}/{name}: expected {want}, drew {got}")

    # Two buttons must clear each other and stay on the panel.
    found, _ = rects(dump, CASES[0][0], CASES[0][1])
    top, bottom = found["top_button_panel"], found["bottom_button_panel"]
    if top[1] + top[3] > bottom[1]:
        failures.append("the dialog's buttons overlap")
    if bottom[1] + bottom[3] > 232:
        failures.append("the dialog's bottom button hangs off the panel")

    for failure in failures:
        print(f"  ! {failure}")
    print("anchors: " + ("ok" if not failures else f"{len(failures)} failure(s)"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
