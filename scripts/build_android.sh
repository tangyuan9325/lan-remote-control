#!/usr/bin/env bash
# Build Android APK
# Usage: ./build_android.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/../android/remote_control_app"

cd "$PROJECT_DIR"

echo "=== Flutter pub get ==="
flutter pub get

echo "=== Building release APK ==="
flutter build apk --release

APK_PATH="$PROJECT_DIR/build/app/outputs/flutter-apk/app-release.apk"
echo ""
echo "=== Build complete ==="
echo "APK: $APK_PATH"

# Copy to release directory
RELEASE_DIR="$SCRIPT_DIR/../release"
mkdir -p "$RELEASE_DIR"
cp "$APK_PATH" "$RELEASE_DIR/LAN-Remote-Control-v1.0.0.apk"
echo "Copied to: $RELEASE_DIR/LAN-Remote-Control-v1.0.0.apk"
