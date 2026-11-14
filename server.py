import http.server
import socketserver
import json
import urllib.parse
import os
import platform
import shutil
import sys
from database import (
    init_db, get_media_files, get_stats, get_all_settings, set_setting,
    add_to_queue, add_all_needing_transcode, get_queue, remove_from_queue,
    clear_completed_queue, get_setting, mark_as_completed, reset_file_status
)
from auth import (
    SESSION_COOKIE_NAME, SESSION_TTL_SECONDS, ensure_admin_initialized,
    authenticate_user, create_session_token, get_authenticated_user,
    set_admin_credentials
)
from scanner import scanner
from queue_manager import queue_manager

PORT = 5500
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

def _get_free_space_gb(target_path):
    """Walks up parent directories until an existing path is found and returns free space in GB."""
    try:
        probe_path = os.path.abspath(target_path)
        while probe_path and not os.path.exists(probe_path):
            parent = os.path.dirname(probe_path)
            if parent == probe_path:
                break
            probe_path = parent
        if probe_path and os.path.exists(probe_path):
            _, _, free = shutil.disk_usage(probe_path)
            return round(free / (1024**3), 1)
    except Exception:
        pass
    return 0.0

def _get_host_hardware_info():
    """Returns CPU and renderer description dynamically based on the host OS."""
    if os.name == "nt":
        return "AMD Radeon RX 6800 (16 GB VRAM)", "AMD Ryzen 7 5700X (8 Cores / 16 Threads)"
    cpu_model = platform.processor() or "Linux x86_64"
    cores = os.cpu_count() or 1
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass
    return f"CPU Renderer ({cores} vCPU • libx264 + libplacebo/lavapipe)", f"{cpu_model} ({cores} vCPU)"

class ConverterHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def _require_auth(self, is_api=False):
        user = get_authenticated_user(self.headers)
        if user:
            return user
        if is_api:
            self.send_json({"error": "Unauthorized"}, status=401)
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
        return None

    def _build_session_cookie(self, token, max_age):
        parts = [
            f"{SESSION_COOKIE_NAME}={token}",
            "HttpOnly",
            "SameSite=Lax",
            "Path=/",
            f"Max-Age={max_age}",
        ]
        if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
            parts.append("Secure")
        return "; ".join(parts)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/login", "/login.html"):
            if get_authenticated_user(self.headers):
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            self.path = "/login.html"
            return super().do_GET()

        if path in ("/style.css", "/static/style.css"):
            self.path = "/style.css"
            return super().do_GET()

        if path in ("/favicon.svg", "/favicon.ico", "/static/favicon.svg"):
            self.path = "/favicon.svg"
            return super().do_GET()

        if not self._require_auth(is_api=path.startswith("/api/")):
            return

        if path == "/":
            self.path = "/index.html"
            return super().do_GET()

        elif path.startswith("/static/"):
            self.path = path.replace("/static/", "/", 1)
            return super().do_GET()

        elif path == "/api/stats":
            self.send_json({
                "stats": get_stats(),
                "settings": get_all_settings(),
                "scanner": scanner.get_status(),
                "queue_active": get_setting("queue_active", "false") == "true"
            })

        elif path == "/api/files":
            filter_param = query.get("filter", ["all"])
            filters = []
            for item in filter_param:
                for f in item.split(","):
                    f = f.strip()
                    if f and f not in filters:
                        filters.append(f)
            if not filters:
                filters = ["all"]
            search = query.get("search", [""])[0]
            limit = int(query.get("limit", [50])[0])
            offset = int(query.get("offset", [0])[0])
            files, total = get_media_files(filters, search, limit, offset)
            self.send_json({"files": files, "total": total, "filters": filters})

        elif path == "/api/queue":
            items = get_queue()
            self.send_json({"queue": items})

        elif path == "/api/system_info":
            default_out = "E:\\Jellyfin_Converted" if os.name == "nt" else "/mnt/gdrive/jellyfin"
            out_dir = get_setting("output_dir", default_out)
            free_gb = _get_free_space_gb(out_dir)
            gpu_label, cpu_label = _get_host_hardware_info()
            self.send_json({
                "gpu": gpu_label,
                "cpu": cpu_label,
                "output_drive_free_gb": free_gb
            })

        else:
            return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self.read_json_body()

        if path == "/api/login":
            username = body.get("username", "")
            password = body.get("password", "")
            if authenticate_user(username, password):
                token = create_session_token(username.strip() or "admin")
                payload = json.dumps({"success": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Set-Cookie", self._build_session_cookie(token, SESSION_TTL_SECONDS))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_json({"success": False, "error": "Username atau password salah."}, status=401)
            return

        if path == "/api/logout":
            payload = json.dumps({"success": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Set-Cookie", self._build_session_cookie("", 0))
            self.end_headers()
            self.wfile.write(payload)
            return

        if not self._require_auth(is_api=True):
            return

        if path == "/api/scan/start":
            default_src = "F:\\" if os.name == "nt" else "/mnt/gdrive/raw"
            req_src = (body.get("source_dir") or "").strip()
            src = req_src or get_setting("source_dir", default_src)
            if req_src:
                set_setting("source_dir", req_src)
            if not os.path.exists(src):
                self.send_json({"success": False, "error": f"Folder sumber tidak ditemukan: {src}"}, status=400)
                return
            success = scanner.start(src)
            if not success:
                self.send_json({"success": False, "error": "Pemindaian sedang berlangsung."})
                return
            self.send_json({"success": True, "source_dir": src})

        elif path == "/api/scan/stop":
            scanner.stop()
            self.send_json({"success": True})

        elif path == "/api/scan/subtitles":
            force = body.get("force", False)
            success = scanner.start_subtitle_scan(force_all=force)
            self.send_json({"success": success})

        elif path == "/api/scan/subtitles_stop":
            scanner.stop()
            self.send_json({"success": True})

        elif path == "/api/queue/add":
            media_id = body.get("media_id")
            success = add_to_queue(media_id)
            self.send_json({"success": success})

        elif path == "/api/queue/add_all":
            count = add_all_needing_transcode()
            self.send_json({"success": True, "added_count": count})

        elif path == "/api/queue/remove":
            queue_id = body.get("queue_id")
            success = remove_from_queue(queue_id)
            self.send_json({"success": success})

        elif path == "/api/queue/clear_completed":
            clear_completed_queue()
            self.send_json({"success": True})

        elif path == "/api/queue/start":
            queue_manager.start_queue()
            self.send_json({"success": True})

        elif path == "/api/queue/pause":
            queue_manager.pause_queue()
            self.send_json({"success": True})

        elif path == "/api/queue/cancel":
            queue_id = body.get("queue_id")
            success = queue_manager.cancel_task(queue_id)
            self.send_json({"success": success})

        elif path == "/api/files/mark_completed":
            media_id = body.get("media_id")
            success = mark_as_completed(media_id)
            self.send_json({"success": success})

        elif path == "/api/files/reset_status":
            media_id = body.get("media_id")
            success = reset_file_status(media_id)
            self.send_json({"success": success})

        elif path == "/api/settings":
            protected_keys = {"admin_username", "admin_password_hash", "session_secret"}
            for k, v in body.items():
                if k not in protected_keys:
                    set_setting(k, v)
            self.send_json({"success": True, "settings": get_all_settings()})

        elif path == "/api/admin/password":
            new_user = body.get("username", "").strip()
            new_pass = body.get("password", "")
            if len(new_pass) < 6:
                self.send_json({"success": False, "error": "Password minimal 6 karakter."}, status=400)
                return
            set_admin_credentials(new_user or get_setting("admin_username", "admin"), new_pass)
            self.send_json({"success": True})

        else:
            self.send_response(404)
            self.end_headers()

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return {}

def run_server():
    init_db()
    ensure_admin_initialized()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORT), ConverterHandler) as httpd:
        print(f"Jellyfin Transcoder Server berjalan di http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer dihentikan.")

if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--set-admin":
        init_db()
        set_admin_credentials(sys.argv[2], sys.argv[3])
        print(f"Admin credentials updated for user: {sys.argv[2]}")
    else:
        run_server()
