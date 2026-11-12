#!/bin/sh
# Renders one of the app's icons, named by its argument, after a change to its SVG; build.sh only copies the .icns,
# and the app embeds the PNG. CHROME names the browser (a headless shell is enough).
#   build-icon.sh icns     icon/Signoff.icns from icon.svg: Chrome draws frame.html at each iconset size, iconutil
#                          packs them. The iconset is left in a temporary folder.
#   build-icon.sh menubar  icon/menubar.png from menubar.svg: the menu bar item's template image, one 44 px PNG,
#                          which Wails sizes to the bar's height (22 points).
set -eu
cd "$(dirname "$0")"
what=${1:-}
case "$what" in
  icns|menubar) ;;
  *) echo "usage: build-icon.sh icns|menubar" >&2; exit 1 ;;
esac
: "${CHROME:?set CHROME to a Chrome or chrome-headless-shell binary}"
work=$(mktemp -d "${TMPDIR:-/tmp}/signoff-icon.XXXXXX")
if [ "$what" = menubar ]; then
  "$CHROME" --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --default-background-color=00000000 --user-data-dir="$work/profile" --window-size=44,44 \
    --screenshot="$work/menubar.png" "file://$(pwd)/menubar.svg" >/dev/null 2>&1
  sips -g pixelWidth -g pixelHeight "$work/menubar.png" | tr -d ' ' | grep -c '^pixel[WH][a-z]*:44$' | grep -qx 2 ||
    { echo "build-icon.sh: menubar.png is not 44 by 44" >&2; exit 1; }
  cp "$work/menubar.png" menubar.png # only a checked render replaces the one in the repo
  echo "build-icon.sh: wrote app/icon/menubar.png" >&2
  exit 0
fi
set="$work/Signoff.iconset"
mkdir "$set"
for n in 16 32 128 256 512; do
  for s in 1 2; do
    px=$((n * s))
    name="icon_${n}x${n}"
    [ "$s" = 1 ] || name="${name}@2x"
    "$CHROME" --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
      --default-background-color=00000000 --user-data-dir="$work/profile" --window-size="$px,$px" \
      --screenshot="$set/$name.png" "file://$(pwd)/frame.html" >/dev/null 2>&1
    # iconutil takes only exact sizes; a browser that clamps the window would hand it a wrong one.
    sips -g pixelWidth -g pixelHeight "$set/$name.png" | tr -d ' ' | grep -c "^pixel[WH][a-z]*:$px$" | grep -qx 2 ||
      { echo "build-icon.sh: $name.png is not $px by $px" >&2; exit 1; }
  done
done
iconutil -c icns -o Signoff.icns "$set"
echo "build-icon.sh: wrote app/icon/Signoff.icns; the iconset is in $set" >&2
