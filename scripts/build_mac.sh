#!/bin/sh
# Build dist/Studio Utility.app and dist/StudioUtility.dmg on the Mac, then run the bundle's self-test.
set -e
cd "$(dirname "$0")/.."
.venv/bin/pip install -q -e ".[gphoto]" pyinstaller
.venv/bin/pyinstaller --noconfirm --log-level WARN installer/studio-utility.spec
"./dist/Studio Utility.app/Contents/MacOS/studio-utility" --self-test

# Canon's SDK can't ship inside the app (licence); on this machine, put it where the installed app looks
fw=$(find vendor/EDSDK -path "*EDSDK.framework" -maxdepth 6 -type d 2>/dev/null | head -1)
if [ -n "$fw" ]; then
  mkdir -p "$HOME/.studio-utility/EDSDK"
  rsync -a --delete "$fw" "$HOME/.studio-utility/EDSDK/"
  echo "Canon SDK available to the app: ~/.studio-utility/EDSDK/EDSDK.framework"
fi

# Drag-to-Applications disk image
stage=$(mktemp -d)
cp -R "dist/Studio Utility.app" "$stage/"
ln -s /Applications "$stage/Applications"
hdiutil create -quiet -ov -fs HFS+ -volname "Studio Utility" -srcfolder "$stage" -format UDZO dist/StudioUtility.dmg
rm -rf "$stage"
echo "Built: $(pwd)/dist/Studio Utility.app"
echo "       $(pwd)/dist/StudioUtility.dmg"
