#!/bin/sh
# Build dist/Studio Utility.app on the Mac and run the bundle's self-test.
set -e
cd "$(dirname "$0")/.."
.venv/bin/pip install -q -e . pyinstaller
.venv/bin/pyinstaller --noconfirm --log-level WARN installer/studio-utility.spec
"./dist/Studio Utility.app/Contents/MacOS/studio-utility" --self-test
echo "Built: $(pwd)/dist/Studio Utility.app"
