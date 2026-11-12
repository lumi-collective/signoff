#!/bin/sh
# Builds app/bin/Signoff.app for macOS from this checkout, ad-hoc signed. No npm: the window loads the
# inbox's own page. The app runs the plugin's signoff command; it is fixed at build time, since an app started from
# the Dock sees no shell PATH. SIGNOFF_LAUNCHER names it (default: this checkout's plugin/bin/signoff).
# SIGNOFF_LAUNCHER=link fixes none: the app then runs what the link the plugin's SessionStart hook keeps in the
# Signoff home points to, so it needs no checkout and follows plugin updates.
set -eu
cd "$(dirname "$0")"
launcher=${SIGNOFF_LAUNCHER:-$(cd .. && pwd)/plugin/bin/signoff}
case "$launcher" in
  link) launcher= ;;
  *\'*|*\"*) echo "build.sh: the signoff command's path may not hold a quote" >&2; exit 1 ;;
  /*) ;;
  *) echo "build.sh: SIGNOFF_LAUNCHER must be an absolute path, or link" >&2; exit 1 ;;
esac
if [ -n "$launcher" ] && { [ ! -f "$launcher" ] || [ ! -x "$launcher" ]; }; then
  echo "build.sh: $launcher is not there or cannot run" >&2; exit 1
fi
version=$(sed -n 's/^ *"version": *"\([^"]*\)".*/\1/p' ../plugin/.claude-plugin/plugin.json | head -n 1)

app="bin/Signoff.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
# As Wails' own macOS build does; production leaves out the web inspector.
CGO_CFLAGS=-mmacosx-version-min=12.0 CGO_LDFLAGS=-mmacosx-version-min=12.0 MACOSX_DEPLOYMENT_TARGET=12.0 \
  go build -tags production -trimpath -ldflags "-s -w -X 'main.launcher=$launcher'" \
  -o "$app/Contents/MacOS/signoff-inbox" .
cp Info.plist "$app/Contents/Info.plist"
cp icon/Signoff.icns "$app/Contents/Resources/Signoff.icns"
plutil -replace CFBundleShortVersionString -string "$version" "$app/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$version" "$app/Contents/Info.plist"
codesign --force --sign - "$app"
echo "build.sh: built app/$app $version; it runs ${launcher:-what the launcher link in the Signoff home points to}" >&2
