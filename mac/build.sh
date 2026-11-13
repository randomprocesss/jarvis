#!/bin/sh
# Builds ~/Applications/Jarvis.app from the Swift files here. Local only: the app is made on, and for, this Mac.
#   JARVIS_HOME      the data home the app should use (default: ~/.jarvis)
#   JARVIS_PYTHON    the Python that runs the server (default: the first python3 on the PATH)
set -e
cd "$(dirname "$0")"
APP="$HOME/Applications/Jarvis.app"
mkdir -p build "$HOME/Applications"

# Icon: render 1024px, then iconset (sips) and .icns (iconutil).
if [ ! -f build/Jarvis.icns ] || [ icon.swift -nt build/Jarvis.icns ]; then
  swift icon.swift build/icon_1024.png
  rm -rf build/Jarvis.iconset && mkdir build/Jarvis.iconset
  for s in 16 32 128 256 512; do
    sips -z $s $s build/icon_1024.png --out build/Jarvis.iconset/icon_${s}x${s}.png >/dev/null
    d=$((s * 2)); sips -z $d $d build/icon_1024.png --out build/Jarvis.iconset/icon_${s}x${s}@2x.png >/dev/null
  done
  iconutil -c icns build/Jarvis.iconset -o build/Jarvis.icns
fi

# Jarvis.swift holds the app's top-level code, which Swift only allows in main.swift once there are several files; every other
# .swift file here (the voice layer and its extensions) is compiled with it, except icon.swift (a script that draws the icon).
cp Jarvis.swift build/main.swift
SRC=$(ls *.swift | grep -v -e '^Jarvis.swift$' -e '^icon.swift$')
swiftc -O -swift-version 5 -target arm64-apple-macos13.0 -o build/Jarvis build/main.swift $SRC \
  -framework AppKit -framework WebKit -framework AVFoundation -framework Speech -framework CoreAudio -framework ScreenCaptureKit -framework Vision -framework SoundAnalysis

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp build/Jarvis "$APP/Contents/MacOS/Jarvis"
cp build/Jarvis.icns "$APP/Contents/Resources/Jarvis.icns"
cp Info.plist "$APP/Contents/Info.plist"
# where the code, the data home and Python are on this Mac (Places.swift reads these; nothing is fixed in the source)
REPO="$(cd .. && pwd)"
PY="${JARVIS_PYTHON:-$(command -v python3 || echo /usr/bin/python3)}"
PB=/usr/libexec/PlistBuddy
$PB -c "Add :JarvisRepo string $REPO" "$APP/Contents/Info.plist"
$PB -c "Add :JarvisPython string $PY" "$APP/Contents/Info.plist"
[ -n "$JARVIS_HOME" ] && $PB -c "Add :JarvisHome string $JARVIS_HOME" "$APP/Contents/Info.plist"
[ -f Jarvis.entitlements ] && ENT="--entitlements Jarvis.entitlements" || ENT=""
# A stable signing identity keeps macOS's permissions (microphone, speech, screen recording) across rebuilds; ad-hoc
# signing makes every build look like a new app, and macOS then feeds it a silent mic (2 Oct). The owner creates the
# certificate once in Keychain Access (name "Jarvis Local Signing", type Code Signing); until then: ad-hoc.
IDENT="Jarvis Local Signing"
# a self-signed identity is "not trusted" (no -v): it still signs, and its stable designated requirement is what keeps
# macOS's permissions across rebuilds (5 Oct 2026: certificate created)
if security find-identity -p codesigning 2>/dev/null | grep -q "\"$IDENT\""; then
  codesign --force --deep -s "$IDENT" $ENT "$APP" && echo "signed with: $IDENT"
else
  codesign --force --deep -s - $ENT "$APP"
  echo "WARNING: no \"$IDENT\" certificate yet: signed ad-hoc, so macOS will ask for the mic again after this build"
fi
codesign -v "$APP" && echo "built + signed: $APP"
