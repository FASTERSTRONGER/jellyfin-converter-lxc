import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from database import upsert_media_file, get_setting, get_connection, update_file_subtitles
from probe import analyze_media, probe_subtitles_fast

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".webm", ".ts", ".m4v"}

class Scanner:
    def __init__(self):
        self.is_scanning = False
        self.stop_requested = False
        self.total_found = 0
        self.scanned_count = 0
        self.current_file = ""
        self.thread = None
        
        # Subtitle scan state
        self.is_scanning_subs = False
        self.sub_stop_requested = False
        self.sub_total = 0
        self.sub_count = 0
        self.sub_current_file = ""
        self.sub_thread = None

    def get_status(self):
        return {
            "is_scanning": self.is_scanning,
            "total_found": self.total_found,
            "scanned_count": self.scanned_count,
            "current_file": self.current_file,
            "sub_scan": {
                "is_scanning": self.is_scanning_subs,
                "total": self.sub_total,
                "count": self.sub_count,
                "current_file": self.sub_current_file
            }
        }

    def start(self, source_dir=None):
        if self.is_scanning:
            return False
        if not source_dir:
            default_src = "F:\\" if os.name == "nt" else "/mnt/gdrive/raw"
            source_dir = get_setting("source_dir", default_src)
            
        self.is_scanning = True
        self.stop_requested = False
        self.total_found = 0
        self.scanned_count = 0
        self.current_file = ""
        
        self.thread = threading.Thread(target=self._run_scan, args=(source_dir,), daemon=True)
        self.thread.start()
        return True

    def stop(self):
        if self.is_scanning:
            self.stop_requested = True
        if self.is_scanning_subs:
            self.sub_stop_requested = True

    def start_subtitle_scan(self, force_all=False):
        """Starts a high-speed parallel subtitle scan across files in database."""
        if self.is_scanning_subs:
            return False
            
        self.is_scanning_subs = True
        self.sub_stop_requested = False
        self.sub_count = 0
        self.sub_current_file = ""
        
        self.sub_thread = threading.Thread(target=self._run_subtitle_scan, args=(force_all,), daemon=True)
        self.sub_thread.start()
        return True

    def _run_subtitle_scan(self, force_all):
        try:
            with get_connection() as conn:
                if force_all:
                    rows = conn.execute("SELECT id, filepath, filename FROM media_files").fetchall()
                else:
                    rows = conn.execute("SELECT id, filepath, filename FROM media_files WHERE subtitle_summary = '' OR subtitle_summary IS NULL").fetchall()
                    
            self.sub_total = len(rows)
            print(f"Starting subtitle scan for {self.sub_total} files...")
            
            def process_item(row):
                if self.sub_stop_requested:
                    return
                try:
                    res = probe_subtitles_fast(row["filepath"])
                    update_file_subtitles(row["id"], res)
                except Exception as e:
                    print(f"Error probing sub for {row['filename']}: {e}")
                finally:
                    self.sub_count += 1
                    self.sub_current_file = row["filename"]

            with ThreadPoolExecutor(max_workers=10) as executor:
                for row in rows:
                    if self.sub_stop_requested:
                        break
                    executor.submit(process_item, row)
                    
        except Exception as e:
            print(f"Error during subtitle scan: {e}")
        finally:
            self.is_scanning_subs = False
            self.sub_current_file = "Pemeriksaan subtitle selesai." if not self.sub_stop_requested else "Dihentikan."
            print(f"Subtitle scan finished. Processed {self.sub_count}/{self.sub_total} files.")

    def _run_scan(self, source_dir):
        print(f"Starting scan on: {source_dir}")
        file_list = []
        try:
            for root, _, files in os.walk(source_dir):
                if self.stop_requested:
                    break
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in VIDEO_EXTENSIONS:
                        full_path = os.path.join(root, f)
                        rel_path = os.path.relpath(full_path, source_dir)
                        file_list.append((full_path, rel_path))
                        self.total_found = len(file_list)
        except Exception as e:
            print(f"Error during directory walk: {e}")

        # Fetch existing files in DB to check for fast caching
        existing_files = {}
        try:
            with get_connection() as conn:
                rows = conn.execute("SELECT filepath, filesize, subtitle_summary FROM media_files").fetchall()
                for r in rows:
                    existing_files[r["filepath"]] = (r["filesize"], bool(r["subtitle_summary"]))
        except Exception as e:
            print(f"Error fetching existing db files: {e}")

        for full_path, rel_path in file_list:
            if self.stop_requested:
                break
                
            self.current_file = os.path.basename(full_path)
            self.scanned_count += 1
            
            try:
                current_size = os.path.getsize(full_path)
                # Fast cache check: if size matches existing record AND subtitles already scanned, skip ffprobe
                if full_path in existing_files:
                    prev_size, has_subs_scanned = existing_files[full_path]
                    if prev_size == current_size and has_subs_scanned:
                        continue
                    
                media_info = analyze_media(full_path, rel_path)
                if media_info:
                    # Strip non-db field before upsert
                    media_data = {k: v for k, v in media_info.items() if k not in ("best_audio_stream_index", "raw_streams")}
                    upsert_media_file(media_data)
            except Exception as e:
                print(f"Failed to process {full_path}: {e}")

        self.is_scanning = False
        self.current_file = "Scan selesai." if not self.stop_requested else "Scan dihentikan."
        print(f"Scan finished. Processed {self.scanned_count} files.")

scanner = Scanner()
