# Jellyfin Media Transcoder (LXC Server Edition)

Aplikasi otomasi transcoding dan audit media berbasis **Python** dan **FFmpeg** yang dirancang untuk berjalan di dalam **Proxmox LXC (Linux CPU-only)** dengan penyimpanan **Google Drive (Rclone)**.

---

## Fitur Utama (Edisi LXC)

1. **Dual-Role (Executor & Format Auditor)**:
   - **Mode Konversi**: Memindai folder input mentah (misal `/mnt/gdrive/raw`) dan menghasilkan output *Direct Play* (H.264 8-bit SDR $\le 1080\text{p}$ + Audio Stereo AAC 256k + Surround Passthrough) ke folder library (`/mnt/gdrive/jellyfin`). File mentah dibiarkan tetap ada dan ditandai `completed` di SQLite.
   - **Mode Audit**: Arahkan *Source Dir* di Web GUI ke `/mnt/gdrive/jellyfin` untuk memeriksa apakah seluruh koleksi di Google Drive sudah kompatibel *Direct Play* atau masih memerlukan konversi.
2. **Presisi `libplacebo` di CPU-only LXC**:
   - Mempertahankan filter `libplacebo` (*EWA Lanczos + Sigmoid + Spline Tone-Mapping*) menggunakan **FFmpeg BtbN Linux64 GPL** dan **Mesa `lavapipe` (software Vulkan)** dengan encoder `libx264`.
3. **Local Disk Staging**:
   - File sementara (`.tmp.mkv`) ditulis ke disk lokal LXC (`/var/cache/jellyfin-converter/staging`), lalu dipindahkan ke mount Google Drive setelah selesai agar terhindar dari galat muxing di atas FUSE.
4. **Rclone Independen Anti-Swap**:
   - Dilengkapi unit `rclone-mount.service` dengan `GODEBUG=madvdontneed=1`, pembatas chunk (`64M`), read-ahead (`128M`), dan batas cache VFS (`25G`).

---

## Persyaratan LXC Proxmox

- **OS**: Debian 12 atau Ubuntu 24.04 (unprivileged LXC)
- **vCPU**: 2–4 vCPU
- **RAM**: 4 GB
- **Swap**: **0 MB** (`pct set <VMID> -swap 0`)
- **Disk**: 50–60 GB (untuk VFS cache Rclone + staging `.tmp.mkv`)
- **Features**: Aktifkan **FUSE** (`nesting=1,fuse=1`)

---

## Instalasi di LXC

```bash
# 1. Salin atau clone proyek ke /opt/jellyfin-converter-lxc
cd /opt/jellyfin-converter-lxc

# 2. Jalankan skrip instalasi
chmod +x deploy/setup_lxc.sh
./deploy/setup_lxc.sh

# 3. Konfigurasi remote rclone bernama 'gdrive-converter'
rclone config

# 4. Aktifkan mount Google Drive dan service converter
systemctl enable --now rclone-mount.service
systemctl start jellyfin-converter.service
```

---

## Mengakses Web GUI

Web GUI berjalan di port **`5500`**:
- **Jaringan Lokal (LAN)**: `http://<IP_LXC>:5500`
- **SSH Port Forwarding dari Terminal PC**:
  ```bash
  ssh -L 5500:localhost:5500 root@<IP_LXC>
  ```
  Lalu buka `http://localhost:5500` di browser PC.
- **Cloudflare Tunnel**: Arahkan subdomain ke `http://<IP_LXC>:5500`.
