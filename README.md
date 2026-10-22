# SolidWorks MCP Server (906grown edition)

Control SolidWorks with plain English through Claude, using the Model Context Protocol (MCP).

**Created by 906grown.** Built on the original [SolidWorks MCP Server](https://github.com/alisamsam/solidworks-mcp) by Samsaam Ali Baig (MIT License).

This edition adds 23 new tools (48 total), a one-click Windows installer, SolidWorks 2026 / 3DEXPERIENCE support, and the ability for Claude to *see* the model with screenshots.

---

## Requirements

- Windows 10 or 11
- SolidWorks 2023 to 2026 (installed and licensed), including SOLIDWORKS 3DEXPERIENCE R2026x
- Python 3.10 or newer ([python.org](https://www.python.org/downloads/), check **"Add python.exe to PATH"** during install)
- The Claude desktop app

## Install (easy way)

1. Download this repo (green **Code** button > **Download ZIP**) and unzip it somewhere permanent, like `Documents\solidworks-mcp`.
2. Double-click **`INSTALL.bat`**. It installs the Python packages, checks the server loads, and adds it to Claude's config (your old config is backed up first).
3. Fully quit Claude (system tray > Quit) and reopen it.
4. Open SolidWorks, then ask Claude: *"Connect to SolidWorks and tell me the version."*

Using Claude to install it? Point it at **[SETUP_FOR_CLAUDE.md](SETUP_FOR_CLAUDE.md)**.

## Manual install

```bash
pip install -r requirements.txt
```

Then add this to `%APPDATA%\Claude\claude_desktop_config.json` (use your real path):

```json
{
  "mcpServers": {
    "solidworks": {
      "command": "python",
      "args": ["C:\\path\\to\\solidworks-mcp\\solidworks_mcp_server.py"]
    }
  }
}
```

---

## Tools

### Original tools (25)

| Category | Tools |
|---|---|
| Connection | `connect_solidworks`, `get_solidworks_info` |
| Documents | `create_new_part`, `create_new_assembly`, `open_document`, `save_document`, `close_document`, `get_document_info`, `list_open_documents` |
| Sketches | `create_sketch`, `create_sketch_on_face`, `close_sketch`, `get_sketch_status`, `draw_line`, `draw_circle`, `draw_rectangle`, `draw_arc`, `draw_polygon` |
| Features | `extrude_sketch`, `cut_extrude`, `fillet_edges`, `chamfer_edges`, `list_features` |
| Utilities | `set_units`, `execute_python` |

### New in the 906grown edition (23)

| Category | Tool | What it does |
|---|---|---|
| See & measure | `take_screenshot` | Captures the model view (any standard view) so Claude can see what it built |
| | `inspect_selection` | Exact geometry of whatever you've selected (diameters, axes, lengths, positions) |
| | `list_round_faces` | Finds holes, pins and fittings with their diameters and positions |
| | `get_bounding_box` | Overall part size and extents |
| Documents | `activate_document` | Switch between open documents |
| Selection | `select_entity` | Select a plane, sketch, feature or axis by name |
| | `select_at_point` | Select the face/edge/vertex at a model coordinate |
| | `clear_selection` | Clear the selection |
| Planes & sketches | `create_plane` | Offset or angled reference planes |
| | `create_sketch_on` | Sketch on any plane or flat face, or the current selection |
| | `create_3d_path` | 3D sketch paths from lines and splines |
| | `draw_circle_at_point` | Dimensioned circle at a model point (e.g. the end of a path) |
| | `draw_centerline` | Construction centerline for revolves |
| | `draw_spline` | Spline through points |
| Advanced features | `sweep` | Sweep (or swept cut) along a path, with sketch or round profile |
| | `loft` | Loft (or lofted cut) between profiles, with optional guides |
| | `revolve` | Revolve (or revolved cut) around a centerline or axis |
| Editing | `list_dimensions` | List dimension names and values |
| | `set_dimension` | Change a dimension by name and rebuild |
| | `rename_feature` | Rename a feature, sketch or plane |
| | `delete_feature` | Delete a feature, sketch or plane |
| | `undo` | Undo the last step(s) |
| | `rebuild` | Force a full rebuild |

Every new tool was tested live against SOLIDWORKS 2026 (API v34).

---

## Example prompts

- "Make a new part, sketch a 30 mm circle on the front plane and extrude it 15 mm."
- "Take an isometric screenshot so I can see it."
- "I selected a hole. What's its diameter?"
- "Sweep a 6 mm round profile along a curved path from (0,0,0) up 40 mm and over 20 mm."
- "List the dimensions and change the socket diameter to 12 mm."

## Notes

- `execute_python` runs any Python code on your computer with access to SolidWorks. Only approve it when you know what it's doing.
- Logs go to `solidworks_mcp/solidworks_mcp.log` (ignored by git).
- If SolidWorks isn't auto-detected, set `exe_path` in `solidworks_mcp/config.json` to your `SLDWORKS.exe`.

## License

MIT. See [LICENSE](LICENSE). Original work copyright Samsaam Ali Baig; additions copyright 906grown.
