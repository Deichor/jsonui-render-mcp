# jsonui-render-mcp

An MCP server that draws Minecraft Bedrock **JSON UI** screens, so a resource pack's layout can be
looked at without a device.

It reads the pack you produced and Mojang's real UI files, expands `@` inheritance and `$variables`,
and reports anything it does not implement instead of guessing.

## Setup

    pip install mcp pillow

Then call the `setup_samples` tool once. It sparse-clones [Mojang/bedrock-samples][s] for the vanilla
UI definitions and textures. Nothing from Mojang is bundled here.

[s]: https://github.com/Mojang/bedrock-samples

Environment:

| Variable | Default | What |
|---|---|---|
| `BEDROCK_SAMPLES` | `~/.cache/bedrock-samples` | the clone above |
| `MINECRAFT_FONT` | `~/.aseprite-mcp/fonts/minecraft` | a folder with `font.json`, `ascii.png` |

Register it:

```json
{
  "mcpServers": {
    "jsonui-render": {
      "command": "python3",
      "args": ["/path/to/jsonui-render-mcp/server.py"]
    }
  }
}
```

## Tools

| Tool | What |
|---|---|
| `setup_samples` | clone Mojang's UI files |
| `list_screens` | the screens a pack dump defines |
| `render_screen` | draw one, with the form's captions and icons |
| `render_control` | draw any control by name, including Mojang's own |
| `check_anchors` | hold the anchor rule to Mojang's dialogs |

## The pack dump

`render_screen` reads a directory your build writes:

    controls.json          name -> control
    screens.json           [{name, width, height, body, title, background, marker}]
    textures/*.png         your own textures, flat
    textures/nineslice.json   name -> border, as an int or [l, t, r, b]

## What it does not do yet

- Bindings other than a form's caption and texture.
- `%cm` is read as the content size, not the largest child.
- Item pictures come from vanilla paths only.

A device is still ground truth. When the two disagree, this is wrong.
