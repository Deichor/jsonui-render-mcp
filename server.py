"""MCP server: draws Minecraft Bedrock JSON UI screens so they can be looked at."""

import json
import tempfile
from pathlib import Path

from mcp.server.mcpserver import MCPServer, Image

from jsonui import anchors, render as R

server = MCPServer("jsonui-render")


def _samples_ready():
    return (R.VANILLA_UI / "_global_variables.json").exists()


def _guard():
    if not _samples_ready():
        return f"Mojang's UI files are not at {R.SAMPLES}. Run setup_samples, or set BEDROCK_SAMPLES."
    if not (R.FONT_DIR / "font.json").exists():
        return f"No font at {R.FONT_DIR}. Set MINECRAFT_FONT to a directory with font.json."
    return None


def _result(png, problems):
    warnings = "\n".join(f"! {p}" for p in dict.fromkeys(problems))
    return [Image(path=png).to_image_content(), warnings or "no warnings"]


@server.tool()
def setup_samples(dest: str = "") -> str:
    """Sparse-clone Mojang's bedrock-samples, which supplies the vanilla UI and textures."""
    import subprocess

    target = Path(dest) if dest else R.SAMPLES
    if _samples_ready():
        return f"already present at {target}"
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--filter=blob:none", "--sparse", "--depth", "1",
         "https://github.com/Mojang/bedrock-samples.git", str(target)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(target), "sparse-checkout", "set",
         "resource_pack/ui", "resource_pack/textures/ui",
         "resource_pack/textures/items", "resource_pack/textures/blocks"],
        check=True, capture_output=True,
    )
    return f"cloned to {target}"


@server.tool()
def render_screen(
    pack: str,
    screen: str,
    entries: list[str],
    title: str = "",
    icons: dict[str, str] | None = None,
) -> list:
    """Draw a screen from a pack dump. `entries` are the form's captions, `icons` their pictures."""
    problem = _guard()
    if problem:
        return [problem]
    png = str(Path(tempfile.mkdtemp()) / f"{screen}.png")
    return _result(png, R.render(pack, screen, entries, title, png, icons))


@server.tool()
def render_control(pack: str, ref: str, width: int = 360, height: int = 232) -> list:
    """Draw any control by name, e.g. `common_dialogs.main_panel_no_buttons`."""
    problem = _guard()
    if problem:
        return [problem]
    png = str(Path(tempfile.mkdtemp()) / "control.png")
    return _result(png, R.render_control(pack, ref, width, height, png))


@server.tool()
def list_screens(pack: str) -> str:
    """The screens a pack dump defines."""
    screens = json.loads((Path(pack) / "screens.json").read_text())
    return "\n".join(f"{s['name']}  {s['width']}x{s['height']}" for s in screens)


@server.tool()
def check_anchors(pack: str) -> str:
    """Check the anchor rule against Mojang's dialogs. Non-zero means the renderer is wrong."""
    problem = _guard()
    if problem:
        return problem
    import io
    import contextlib

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        anchors.main(pack)
    return out.getvalue().strip()


def main():
    server.run()


if __name__ == "__main__":
    main()
