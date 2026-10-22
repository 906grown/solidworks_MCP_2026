# Setup instructions for Claude

These instructions are for Claude (Claude Code, or Claude desktop with a computer/terminal tool) helping a user install this SolidWorks MCP server on Windows. Go one step at a time and confirm each step worked before moving on. The user may be new to this.

## 1. Check prerequisites

- Confirm Windows 10/11 and that SolidWorks 2023 to 2026 is installed and opens normally.
- Run `py -3 --version` (or `python --version`). Need 3.10+. If missing, have the user install from https://www.python.org/downloads/ with **"Add python.exe to PATH"** checked, then open a new terminal.
- Confirm the Claude desktop app is installed.

## 2. Put the folder somewhere permanent

The repo folder must stay where it is after install (Claude's config points to it). `Documents\solidworks-mcp` is a good spot. Don't run it from inside a ZIP or the Downloads folder if the user cleans that out.

## 3. Run the installer

From the repo folder, run `INSTALL.bat` (or `py -3 setup_claude.py`). It:
1. `pip install`s `mcp` and `pywin32` from `requirements.txt`
2. Imports `solidworks_mcp.server` to check it loads (prints `Server OK`)
3. Adds a `solidworks` entry to `claude_desktop_config.json` (both the normal `%APPDATA%\Claude` location and the Microsoft Store app location if present), backing up any existing config first

If step 3 fails because the existing config isn't valid JSON, fix the JSON (don't delete the user's other MCP servers) and run it again.

## 4. Restart Claude

Fully quit Claude from the system tray (closing the window isn't enough) and reopen it.

## 5. Test

1. Open SolidWorks.
2. In Claude, check the `solidworks` tools are listed, then call `connect_solidworks` and `get_solidworks_info`.
3. Quick build test: `create_new_part` > `create_sketch` (Front) > `draw_circle` radius 10 > `extrude_sketch` depth 10 > `take_screenshot` view isometric.

## Troubleshooting

- **Tools don't appear in Claude:** Check the config JSON path points to `solidworks_mcp_server.py` and the Python path in `command` exists. Fully restart Claude.
- **"No module named win32com" / pywintypes errors:** Run `py -3 -m pip install --upgrade pywin32`, then `py -3 -m pywin32_postinstall -install` from an admin terminal.
- **Can't find SolidWorks:** Set `exe_path` in `solidworks_mcp/config.json` to the full path of `SLDWORKS.exe`.
- **Template errors on new part:** SolidWorks needs default templates set (Tools > Options > Default Templates).
- **Server log:** `solidworks_mcp/solidworks_mcp.log`.
