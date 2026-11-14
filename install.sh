#!/bin/sh
# Install Jarvis for the person running this, on macOS or Linux. It checks what Jarvis needs, adds the `jarvis` command,
# starts Jarvis and opens it in the browser. Everything after that is in the app (Setup).
#
# It never uses sudo, downloads nothing and changes nothing outside two places: your data folder (~/.jarvis, or
# JARVIS_HOME) and one small launcher file, ~/.local/bin/jarvis.
#
#     ./install.sh             install and start
#     ./install.sh --no-start  install only
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
say() { printf '%s\n' "$*"; }

PY=""
for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY=$(command -v "$c"); break; fi
done
if [ -z "$PY" ]; then
  say "Jarvis needs Python 3.9 or newer, and none was found."
  say "On a Mac: install Apple's command line tools (xcode-select --install) or Python from python.org, then run this again."
  exit 1
fi
command -v git >/dev/null 2>&1 || say "Note: git was not found. Jarvis runs without it, but it cannot update itself or read your repositories."

BIN="$HOME/.local/bin"
mkdir -p "$BIN"
cat > "$BIN/jarvis" <<LAUNCH
#!/bin/sh
# The jarvis command (written by install.sh). It runs the Jarvis in $HERE.
cd "$HERE" && exec "$PY" -m jarvis "\$@"
LAUNCH
chmod 755 "$BIN/jarvis"
say "The jarvis command is at $BIN/jarvis"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) say "That folder is not on your PATH yet. Until it is, run Jarvis as: $BIN/jarvis"
     case "${SHELL:-}" in
       */zsh)  say "To add it: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc, then open a new terminal." ;;
       */bash) say "To add it: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc, then open a new terminal." ;;
     esac ;;
esac

cd "$HERE"
"$PY" -m jarvis doctor || say "Some things above need fixing. Jarvis starts anyway; Setup in the app shows the same list."
if [ "${1:-}" != "--no-start" ]; then
  "$PY" -m jarvis start
  say "Jarvis opens on its Setup page. To look around on made-up data first: jarvis demo"
fi
