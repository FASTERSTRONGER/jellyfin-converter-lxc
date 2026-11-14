#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/jellyfin-converter-lxc"
STAGING_DIR="/var/cache/jellyfin-converter/staging"
MOUNT_DIR="/mnt/gdrive"
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"

echo "[1/5] Installing system packages (Python3, FUSE3, Mesa Lavapipe Vulkan)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    python3 \
    mesa-vulkan-drivers \
    libvulkan1 \
    vulkan-tools \
    fuse3 \
    curl \
    ca-certificates \
    xz-utils

if ! command -v rclone >/dev/null 2>&1; then
    echo "[2/5] Installing rclone..."
    curl -fsSL https://rclone.org/install.sh | bash
else
    echo "[2/5] rclone already installed."
fi

echo "[3/5] Installing BtbN static FFmpeg (with libplacebo + Vulkan)..."
TMP_TAR=$(mktemp -d)
curl -fsSL "$FFMPEG_URL" -o "$TMP_TAR/ffmpeg.tar.xz"
tar -xf "$TMP_TAR/ffmpeg.tar.xz" -C "$TMP_TAR"
EXTRACTED_DIR=$(find "$TMP_TAR" -maxdepth 1 -type d -name "ffmpeg-*" | head -n 1)
install -m 0755 "$EXTRACTED_DIR/bin/ffmpeg" /usr/local/bin/ffmpeg
install -m 0755 "$EXTRACTED_DIR/bin/ffprobe" /usr/local/bin/ffprobe
rm -rf "$TMP_TAR"

echo "[4/5] Preparing directories and FUSE configuration..."
mkdir -p "$APP_DIR" "$STAGING_DIR" "$MOUNT_DIR"
if [ -f /etc/fuse.conf ] && ! grep -q "^user_allow_other" /etc/fuse.conf; then
    sed -i 's/^#user_allow_other/user_allow_other/' /etc/fuse.conf
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
if [ "$PROJECT_ROOT" != "$APP_DIR" ]; then
    cp -r "$PROJECT_ROOT"/* "$APP_DIR/"
fi

echo "[5/5] Installing systemd services..."
cp "$APP_DIR/deploy/rclone-mount.service" /etc/systemd/system/rclone-mount.service
cp "$APP_DIR/deploy/jellyfin-converter.service" /etc/systemd/system/jellyfin-converter.service
systemctl daemon-reload
systemctl enable jellyfin-converter.service

echo ""
echo "=== Setup Selesai ==="
echo "Verifikasi Vulkan (lavapipe) : vulkaninfo --summary | grep -i deviceName"
echo "Verifikasi libplacebo        : /usr/local/bin/ffmpeg -hide_banner -filters | grep libplacebo"
echo "Langkah berikutnya:"
echo "  1. Konfigurasi remote rclone bernama 'gdrive-converter': rclone config"
echo "  2. Aktifkan mount: systemctl enable --now rclone-mount.service"
echo "  3. Jalankan converter: systemctl start jellyfin-converter.service"
echo "  4. Akses Web GUI di port 5500 (IP LXC / SSH Tunnel / Cloudflare Tunnel)"
