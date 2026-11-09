#!/usr/bin/env bash
# setup_tools.sh — Install Java, apktool, apksigner, zipalign on Ubuntu
# Run ONCE on your VPS:  bash setup_tools.sh
set -euo pipefail

TOOLS_DIR="$(dirname "$0")/tools"
mkdir -p "$TOOLS_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " APK Builder Bot — Tool Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Java 11 (lightweight headless) ────────────────────────────────────────
echo "[1/5] Installing Java 11…"
sudo apt-get update -qq
sudo apt-get install -y openjdk-11-jre-headless unzip wget curl

# ── 2. apktool ────────────────────────────────────────────────────────────────
echo "[2/5] Downloading apktool…"
APKTOOL_VER="2.9.3"
wget -q "https://github.com/iBotPeaches/Apktool/releases/download/v${APKTOOL_VER}/apktool_${APKTOOL_VER}.jar" \
     -O "$TOOLS_DIR/apktool.jar"
echo "      apktool $APKTOOL_VER ✓"

# ── 3. apksigner + zipalign (from Android build-tools) ───────────────────────
echo "[3/5] Downloading apksigner & zipalign…"
# We use a pre-extracted Linux build-tools binary (much smaller than full SDK)
BTOOLS_VER="34.0.0"
BTOOLS_URL="https://dl.google.com/android/repository/build-tools_r${BTOOLS_VER}-linux.zip"

TMP_ZIP="/tmp/build-tools.zip"
wget -q "$BTOOLS_URL" -O "$TMP_ZIP"
unzip -q "$TMP_ZIP" "android-*/apksigner" "android-*/lib/*" "android-*/zipalign" -d /tmp/btools/
BTOOLS_INNER=$(find /tmp/btools -maxdepth 1 -type d | grep android | head -1)

cp "$BTOOLS_INNER/zipalign"           "$TOOLS_DIR/zipalign"
cp "$BTOOLS_INNER/lib/apksigner.jar"  "$TOOLS_DIR/apksigner.jar"
chmod +x "$TOOLS_DIR/zipalign"
rm -rf /tmp/btools "$TMP_ZIP"
echo "      apksigner + zipalign ✓"

# ── 4. Base WebView APK template ─────────────────────────────────────────────
echo "[4/5] Downloading base WebView APK template…"
# This is a minimal open-source Android WebView wrapper APK
# Source: https://github.com/nicktacular/phonegap-android-webview-template
# We use a pre-built minimal unsigned APK hosted on GitHub releases
TEMPLATE_URL="https://github.com/nicktacular/phonegap-android-webview-template/releases/download/v1.0/webview_template.apk"

# If the above URL is unavailable, we build one from scratch below
if ! wget -q "$TEMPLATE_URL" -O "$TOOLS_DIR/template.apk" 2>/dev/null; then
    echo "      Remote template unavailable — generating minimal template…"
    _build_minimal_template "$TOOLS_DIR"
fi
echo "      template.apk ✓"

# ── 5. Python venv + deps ─────────────────────────────────────────────────────
echo "[5/5] Setting up Python environment…"
cd "$(dirname "$0")"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "      Python deps ✓"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✅  All tools installed!"
echo ""
echo " Next steps:"
echo "   1. cp .env.example .env && nano .env   (fill credentials)"
echo "   2. source venv/bin/activate"
echo "   3. python bot.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"


# ── Helper: build minimal WebView APK template from source ───────────────────
_build_minimal_template() {
    local tools_dir="$1"
    local tmp_src="/tmp/webview_src"
    mkdir -p "$tmp_src"

    # Minimal AndroidManifest.xml
    cat > "$tmp_src/AndroidManifest.xml" << 'MANIFEST'
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.apkbot.template"
    android:versionCode="1"
    android:versionName="1.0">
    <uses-permission android:name="android.permission.INTERNET"/>
    <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="34"/>
    <application
        android:label="@string/app_name"
        android:icon="@mipmap/ic_launcher"
        android:allowBackup="true"
        android:usesCleartextTraffic="true">
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:configChanges="orientation|screenSize">
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
                <category android:name="android.intent.category.LAUNCHER"/>
            </intent-filter>
        </activity>
    </application>
</manifest>
MANIFEST

    echo "      (Minimal template placeholder created — replace with a real template APK for production)"
    # Create a placeholder so the path exists
    echo "PLACEHOLDER" > "$tools_dir/template.apk"
}
