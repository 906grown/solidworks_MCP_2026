"""Installs dependencies and registers this SolidWorks MCP server with the Claude desktop app."""
import json, os, subprocess, sys, shutil, datetime
here = os.path.dirname(os.path.abspath(__file__))
print(f"Python: {sys.executable} ({sys.version.split()[0]})")
if sys.version_info < (3, 10):
    sys.exit("Python 3.10 or newer is required.")
print("\n[1/3] Installing packages...")
subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "-r", os.path.join(here, "requirements.txt")])
print("\n[2/3] Checking the server loads...")
subprocess.check_call([sys.executable, "-c", "import sys; sys.path.insert(0, r'%s'); import solidworks_mcp.server; print('Server OK')" % here])
print("\n[3/3] Registering with Claude desktop...")
appdata, local = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
candidates = [os.path.join(appdata, "Claude")]
pk = os.path.join(local, "Packages")
if os.path.isdir(pk):
    candidates += [os.path.join(pk, d, "LocalCache", "Roaming", "Claude") for d in os.listdir(pk) if d.lower().startswith("claude")]
# Newer Claude desktop builds keep settings in %LOCALAPPDATA%\Claude-Data\<id>
cd = os.path.join(local, "Claude-Data")
if os.path.isdir(cd):
    candidates += [os.path.join(cd, d) for d in os.listdir(cd) if os.path.isdir(os.path.join(cd, d))]
targets = [d for d in candidates if os.path.isdir(d)] or [candidates[0]]
entry = {"command": sys.executable, "args": [os.path.join(here, "solidworks_mcp_server.py")]}
for d in targets:
    os.makedirs(d, exist_ok=True)
    cfg = os.path.join(d, "claude_desktop_config.json")
    data = {}
    if os.path.exists(cfg):
        shutil.copy(cfg, cfg + ".backup-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
        try:
            data = json.load(open(cfg, encoding="utf-8"))
        except Exception:
            sys.exit(f"Could not read {cfg}; it was left unchanged.")
    data.setdefault("mcpServers", {})["solidworks"] = entry
    json.dump(data, open(cfg, "w", encoding="utf-8"), indent=2)
    print(f"  Updated {cfg}")
print("\nDone! Fully quit Claude (system tray > Quit) and reopen it.")
