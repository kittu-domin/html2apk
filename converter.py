"""
converter.py — HTML → APK conversion engine

Flow:
  1. Download HTML/ZIP from Telegram
  2. Inject into base WebView APK template (via apktool)
  3. Auto-generate keystore (once) and sign the APK
  4. Return path to signed APK

Requirements (installed by setup_tools.sh):
  - Java 11+
  - apktool
  - apksigner (from Android build-tools)
  - zipalign (from Android build-tools)
"""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("apk_bot.converter")

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
TOOLS_DIR     = BASE_DIR / "tools"
TEMPLATE_APK  = TOOLS_DIR / "template.apk"       # base WebView APK
KEYSTORE_PATH = BASE_DIR  / "apk_bot.keystore"
WORK_DIR      = BASE_DIR  / "builds"             # temporary build workspace

APKTOOL    = TOOLS_DIR / "apktool.jar"
APKSIGNER  = TOOLS_DIR / "apksigner.jar"
ZIPALIGN   = TOOLS_DIR / "zipalign"

KEYSTORE_ALIAS    = "apkbotkey"
KEYSTORE_PASS     = "apkbot2024"
KEYSTORE_DNAME    = "CN=APKBot, OU=Bot, O=APKBot, L=IN, S=IN, C=IN"

WORK_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Keystore (generated once, reused for all APKs)
# ═══════════════════════════════════════════════════════════════════════════════
def ensure_keystore():
    if KEYSTORE_PATH.exists():
        return
    logger.info("Generating keystore (first run)…")
    cmd = [
        "keytool", "-genkey", "-v",
        "-keystore",  str(KEYSTORE_PATH),
        "-alias",     KEYSTORE_ALIAS,
        "-keyalg",    "RSA",
        "-keysize",   "2048",
        "-validity",  "10000",
        "-storepass", KEYSTORE_PASS,
        "-keypass",   KEYSTORE_PASS,
        "-dname",     KEYSTORE_DNAME,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info(f"Keystore created at {KEYSTORE_PATH}")


# ═══════════════════════════════════════════════════════════════════════════════
# Java / apktool helpers
# ═══════════════════════════════════════════════════════════════════════════════
def _java(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["java", "-Xmx256m", "-jar", *[str(a) for a in args]],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def _run(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in args],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Main conversion function
# ═══════════════════════════════════════════════════════════════════════════════
async def convert_to_apk(
    html_path: str,          # local path to downloaded HTML or ZIP
    app_name: str,
    icon_path: Optional[str] = None,
    source_type: str = "html",
) -> str:
    """
    Convert an HTML file (or ZIP with index.html) into a signed APK.
    Returns the path to the signed APK file.
    Raises RuntimeError on failure.
    """
    ensure_keystore()

    # Run blocking work in a thread pool so the bot stays async
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _build_apk_sync,
        html_path,
        app_name,
        icon_path,
        source_type,
    )


def _build_apk_sync(
    html_path: str,
    app_name: str,
    icon_path: Optional[str],
    source_type: str,
) -> str:
    build_id  = _safe_name(app_name)
    build_dir = WORK_DIR / build_id
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    decompiled = build_dir / "decompiled"
    unsigned   = build_dir / f"{build_id}_unsigned.apk"
    aligned    = build_dir / f"{build_id}_aligned.apk"
    signed     = build_dir / f"{build_id}.apk"

    try:
        # ── 1. Decompile template APK ────────────────────────────────────────
        logger.info(f"[{build_id}] Decompiling template…")
        _java(APKTOOL, "d", TEMPLATE_APK, "-o", decompiled, "-f")

        # ── 2. Inject HTML content ───────────────────────────────────────────
        assets_dir = decompiled / "assets"
        assets_dir.mkdir(exist_ok=True)
        www_dir = assets_dir / "www"
        www_dir.mkdir(exist_ok=True)

        if source_type == "zip":
            logger.info(f"[{build_id}] Extracting ZIP…")
            with zipfile.ZipFile(html_path, "r") as zf:
                zf.extractall(str(www_dir))
            # Ensure there's an index.html at root
            if not (www_dir / "index.html").exists():
                # Try to find one nested inside
                found = list(www_dir.rglob("index.html"))
                if found:
                    # Flatten: move everything up
                    nested = found[0].parent
                    for item in nested.iterdir():
                        shutil.move(str(item), str(www_dir))
                    shutil.rmtree(str(nested))
                else:
                    raise RuntimeError("ZIP has no index.html")
        else:
            # Single HTML file
            shutil.copy(html_path, str(www_dir / "index.html"))

        # ── 3. Patch app name in strings.xml ────────────────────────────────
        logger.info(f"[{build_id}] Patching app name…")
        _patch_app_name(decompiled, app_name)

        # ── 4. Replace icon (if provided) ────────────────────────────────────
        if icon_path and os.path.exists(icon_path):
            logger.info(f"[{build_id}] Replacing icon…")
            _replace_icon(decompiled, icon_path)

        # ── 5. Patch package name to be unique ───────────────────────────────
        _patch_package(decompiled, f"com.apkbot.{build_id.lower()[:20]}")

        # ── 6. Recompile APK ─────────────────────────────────────────────────
        logger.info(f"[{build_id}] Recompiling APK…")
        _java(APKTOOL, "b", decompiled, "-o", unsigned)

        # ── 7. Zipalign ──────────────────────────────────────────────────────
        logger.info(f"[{build_id}] Zipaligning…")
        _run(ZIPALIGN, "-v", "-p", "4", unsigned, aligned)

        # ── 8. Sign APK ──────────────────────────────────────────────────────
        logger.info(f"[{build_id}] Signing APK…")
        _java(
            APKSIGNER, "sign",
            "--ks",          KEYSTORE_PATH,
            "--ks-pass",     f"pass:{KEYSTORE_PASS}",
            "--ks-key-alias", KEYSTORE_ALIAS,
            "--key-pass",    f"pass:{KEYSTORE_PASS}",
            "--out",         signed,
            aligned,
        )

        logger.info(f"[{build_id}] ✅ APK ready: {signed}")
        return str(signed)

    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        logger.error(f"[{build_id}] Build failed:\n{stderr}")
        raise RuntimeError(f"Build failed: {stderr[-300:]}")
    except Exception as e:
        logger.error(f"[{build_id}] Error: {e}")
        raise
    finally:
        # Clean up intermediate files but keep the signed APK
        for p in [decompiled, unsigned, aligned]:
            try:
                if isinstance(p, Path) and p.exists():
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# Patch helpers
# ═══════════════════════════════════════════════════════════════════════════════
def _patch_app_name(decompiled: Path, app_name: str):
    strings_file = decompiled / "res" / "values" / "strings.xml"
    if not strings_file.exists():
        return
    content = strings_file.read_text(encoding="utf-8")
    import re
    # Replace app_name string
    content = re.sub(
        r'(<string name="app_name">)[^<]*(</string>)',
        rf'\g<1>{_xml_escape(app_name)}\g<2>',
        content,
    )
    strings_file.write_text(content, encoding="utf-8")


def _patch_package(decompiled: Path, package: str):
    manifest = decompiled / "AndroidManifest.xml"
    if not manifest.exists():
        return
    import re
    content = manifest.read_text(encoding="utf-8")
    content = re.sub(r'package="[^"]*"', f'package="{package}"', content, count=1)
    manifest.write_text(content, encoding="utf-8")

    apktool_yml = decompiled / "apktool.yml"
    if apktool_yml.exists():
        content = apktool_yml.read_text(encoding="utf-8")
        content = re.sub(r"(renameManifestPackage: ).*", rf"\1'{package}'", content)
        apktool_yml.write_text(content, encoding="utf-8")


def _replace_icon(decompiled: Path, icon_path: str):
    """Replace all mipmap launcher icons with the user-supplied image."""
    from PIL import Image
    sizes = {
        "mipmap-mdpi":    48,
        "mipmap-hdpi":    72,
        "mipmap-xhdpi":   96,
        "mipmap-xxhdpi":  144,
        "mipmap-xxxhdpi": 192,
    }
    res_dir = decompiled / "res"
    for folder, size in sizes.items():
        target_dir = res_dir / folder
        if not target_dir.exists():
            continue
        # Find existing icon file (ic_launcher.png or similar)
        for existing in target_dir.glob("ic_launcher*.png"):
            try:
                img = Image.open(icon_path).convert("RGBA").resize(
                    (size, size), Image.LANCZOS
                )
                img.save(str(existing), "PNG")
            except Exception as e:
                logger.warning(f"Icon replace failed for {existing}: {e}")


def _safe_name(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)[:30]


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Cleanup helper (call after sending APK to user)
# ═══════════════════════════════════════════════════════════════════════════════
def cleanup_build(apk_path: str):
    try:
        p = Path(apk_path)
        if p.exists():
            p.unlink()
        # Also remove parent build dir if empty
        parent = p.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception as e:
        logger.warning(f"Cleanup failed: {e}")
