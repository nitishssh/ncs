#!/bin/sh
# Build dist/camctl.app on the Mac and run the bundle's self-test.
set -e
cd "$(dirname "$0")/.."
.venv/bin/pip install -q -e . pyinstaller
.venv/bin/pyinstaller --noconfirm --log-level WARN installer/camctl.spec
./dist/camctl.app/Contents/MacOS/camctl --self-test
echo "Built: $(pwd)/dist/camctl.app"
