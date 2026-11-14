import http.server
import socketserver
import json
import urllib.parse
import os
import platform
import shutil
from database import (
    init_db, get_media_files, get_stats, get_all_settings, set_setting,
    add_to_queue, add_all_needing_transcode, get_queue, remove_from_queue,
    clear_completed_queue, get_setting, mark_as_completed, reset_file_status
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

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

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

        if path == "/api/scan/start":
            default_src = "F:\\" if os.name == "nt" else "/mnt/gdrive/raw"
            src = body.get("source_dir") or get_setting("source_dir", default_src)
            success = scanner.start(src)
            self.send_json({"success": success})

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
            for k, v in body.items():
                set_setting(k, v)
            self.send_json({"success": True, "settings": get_all_settings()})

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
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORT), ConverterHandler) as httpd:
        print(f"Jellyfin Transcoder Server berjalan di http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer dihentikan.")

if __name__ == "__main__":
    run_server()
