import subprocess
import threading
import time
import os
import shutil
from database import (
    init_db, get_connection, get_setting, set_setting, reset_running_to_pending
)
from transcoder import build_transcode_command

class QueueManager:
    def __init__(self):
        self.is_running = False
        self.active_processes = {}  # queue_id -> subprocess.Popen
        self.lock = threading.Lock()
        self.workers = []
        self.worker_stop_events = []
        
        # Ensure database schema exists before recovering interrupted tasks on fresh LXC deploy
        init_db()
        reset_running_to_pending()

    def start_queue(self):
        set_setting("queue_active", "true")
        if self.is_running:
            return
            
        self.is_running = True
        concurrency = int(get_setting("concurrent_workers", "2"))
        self.workers = []
        self.worker_stop_events = []
        
        for w_id in range(concurrency):
            stop_evt = threading.Event()
            t = threading.Thread(target=self._worker_loop, args=(w_id, stop_evt), daemon=True)
            self.workers.append(t)
            self.worker_stop_events.append(stop_evt)
            t.start()
            
        print(f"Queue started with {concurrency} workers.")

    def pause_queue(self):
        set_setting("queue_active", "false")
        self.is_running = False
        for evt in self.worker_stop_events:
            evt.set()
        print("Queue paused. Running workers will exit after finishing their current tasks.")

    def _get_temp_output_path(self, queue_id, out_path):
        staging_dir = get_setting("staging_dir", "").strip()
        if staging_dir:
            os.makedirs(staging_dir, exist_ok=True)
            base_name = os.path.basename(out_path)
            return os.path.join(staging_dir, f"q{queue_id}_{base_name}.tmp.mkv")
        return out_path + ".tmp.mkv"

    def cancel_task(self, queue_id):
        with self.lock:
            proc = self.active_processes.get(queue_id)
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
                    
            with get_connection() as conn:
                row = conn.execute("SELECT output_path, media_id FROM transcode_queue WHERE id = ?", (queue_id,)).fetchone()
                if row:
                    tmp_file = self._get_temp_output_path(queue_id, row["output_path"])
                    if os.path.exists(tmp_file):
                        try:
                            os.remove(tmp_file)
                        except Exception:
                            pass
                    conn.execute("UPDATE transcode_queue SET status = 'cancelled', progress_percent = 0.0 WHERE id = ?", (queue_id,))
                    conn.execute("UPDATE media_files SET status = 'scanned' WHERE id = ?", (row["media_id"],))
                    conn.commit()
        return True

    def _worker_loop(self, worker_id, stop_event):
        while not stop_event.is_set():
            if get_setting("queue_active", "false") != "true":
                break
                
            task = self._claim_next_task(worker_id)
            if not task:
                time.sleep(2)
                continue
                
            self._process_task(task, worker_id)

    def _claim_next_task(self, worker_id):
        with self.lock:
            with get_connection() as conn:
                row = conn.execute("""
                    SELECT q.id as queue_id, q.media_id, q.output_path, q.encoder,
                           m.filepath, m.duration_sec, m.filename
                    FROM transcode_queue q
                    JOIN media_files m ON q.media_id = m.id
                    WHERE q.status = 'pending'
                    ORDER BY q.id ASC
                    LIMIT 1
                """).fetchone()
                
                if row:
                    now = time.strftime("%Y-%m-%dT%H:%M:%S")
                    conn.execute("""
                        UPDATE transcode_queue
                        SET status = 'running', worker_id = ?, started_at = ?
                        WHERE id = ?
                    """, (worker_id, now, row["queue_id"]))
                    conn.execute("UPDATE media_files SET status = 'converting' WHERE id = ?", (row["media_id"],))
                    conn.commit()
                    return dict(row)
        return None

    def _process_task(self, task, worker_id):
        queue_id = task["queue_id"]
        media_id = task["media_id"]
        in_path = task["filepath"]
        out_path = task["output_path"]
        default_enc = "h264_amf" if os.name == "nt" else "libx264"
        encoder = task["encoder"] or get_setting("encoder", default_enc)
        duration = float(task["duration_sec"]) or 1.0
        crf_qp = get_setting("crf_qp", "21")
        audio_bitrate = get_setting("audio_bitrate", "256k")
        
        # Ensure target directory exists
        dest_dir = os.path.dirname(out_path)
        os.makedirs(dest_dir, exist_ok=True)
        
        tmp_out = self._get_temp_output_path(queue_id, out_path)
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except Exception:
                pass
                
        try:
            cmd = build_transcode_command(
                input_path=in_path,
                output_path=tmp_out,
                encoder=encoder,
                qp_crf=crf_qp,
                audio_bitrate=audio_bitrate
            )
            
            popen_kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                popen_kwargs["preexec_fn"] = lambda: os.nice(15)

            proc = subprocess.Popen(cmd, **popen_kwargs)
            
            with self.lock:
                self.active_processes[queue_id] = proc

            # Continuously drain stderr in a background thread to prevent pipe buffer deadlock
            stderr_buffer = []
            def drain_stderr(pipe):
                try:
                    for line in iter(pipe.readline, ''):
                        stderr_buffer.append(line)
                        if len(stderr_buffer) > 60:
                            stderr_buffer.pop(0)
                except Exception:
                    pass
                finally:
                    try:
                        pipe.close()
                    except Exception:
                        pass

            stderr_thread = threading.Thread(target=drain_stderr, args=(proc.stderr,), daemon=True)
            stderr_thread.start()

            start_time = time.time()
            last_update = time.time()
            current_progress = 0.0
            current_fps = 0.0
            current_speed = ""
            eta_sec = 0

            # Read progress from stdout line by line
            for line in proc.stdout:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k == "out_time_us":
                        try:
                            cur_time_sec = float(v) / 1000000.0
                            current_progress = min(99.9, round((cur_time_sec / duration) * 100.0, 1))
                            if cur_time_sec > 0 and current_progress > 0:
                                elapsed = time.time() - start_time
                                total_est = elapsed / (current_progress / 100.0)
                                eta_sec = max(0, int(total_est * (1.0 - (current_progress / 100.0))))
                        except Exception:
                            pass
                    elif k == "fps":
                        try:
                            current_fps = float(v)
                        except Exception:
                            pass
                    elif k == "speed":
                        current_speed = v

                now = time.time()
                if now - last_update >= 1.0:
                    last_update = now
                    with get_connection() as conn:
                        conn.execute("""
                            UPDATE transcode_queue
                            SET progress_percent = ?, current_fps = ?, current_speed = ?, eta_seconds = ?
                            WHERE id = ?
                        """, (current_progress, current_fps, current_speed, eta_sec, queue_id))
                        conn.commit()

            proc.wait()
            try:
                stderr_thread.join(timeout=2.0)
            except Exception:
                pass
            stderr_out = "".join(stderr_buffer)

            with self.lock:
                self.active_processes.pop(queue_id, None)

            if proc.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
                # Move from local staging (or temp path) to final destination
                if os.path.exists(out_path):
                    os.remove(out_path)
                shutil.move(tmp_out, out_path)
                
                # Copy companion external subtitle files if present
                try:
                    with get_connection() as conn:
                        sub_row = conn.execute("SELECT external_subs FROM media_files WHERE id = ?", (media_id,)).fetchone()
                        if sub_row and sub_row["external_subs"]:
                            import json
                            ext_subs = json.loads(sub_row["external_subs"])
                            out_dir = os.path.dirname(out_path)
                            out_stem = os.path.splitext(os.path.basename(out_path))[0]
                            for s in ext_subs:
                                s_src = s.get("path")
                                if s_src and os.path.exists(s_src):
                                    s_ext = s.get("ext", "srt")
                                    s_lang = s.get("language", "").lower()
                                    if s_lang and s_lang != "und":
                                        s_dest_name = f"{out_stem}.{s_lang}.{s_ext}"
                                    else:
                                        s_dest_name = f"{out_stem}.{s_ext}"
                                    s_dest_path = os.path.join(out_dir, s_dest_name)
                                    shutil.copy2(s_src, s_dest_path)
                except Exception as sub_err:
                    print(f"Notice: Could not copy external subtitle: {sub_err}")

                fin_time = time.strftime("%Y-%m-%dT%H:%M:%S")
                with get_connection() as conn:
                    conn.execute("""
                        UPDATE transcode_queue
                        SET status = 'completed', progress_percent = 100.0, current_speed = '', eta_seconds = 0, finished_at = ?
                        WHERE id = ?
                    """, (fin_time, queue_id))
                    conn.execute("UPDATE media_files SET status = 'completed' WHERE id = ?", (media_id,))
                    conn.commit()
            else:
                if os.path.exists(tmp_out):
                    try:
                        os.remove(tmp_out)
                    except Exception:
                        pass
                err_snippet = stderr_out[-1000:] if stderr_out else "FFmpeg exited with error"
                with get_connection() as conn:
                    conn.execute("""
                        UPDATE transcode_queue
                        SET status = 'failed', error_message = ?
                        WHERE id = ?
                    """, (err_snippet, queue_id))
                    conn.execute("UPDATE media_files SET status = 'failed' WHERE id = ?", (media_id,))
                    conn.commit()

        except Exception as e:
            if os.path.exists(tmp_out):
                try:
                    os.remove(tmp_out)
                except Exception:
                    pass
            with self.lock:
                self.active_processes.pop(queue_id, None)
            with get_connection() as conn:
                conn.execute("""
                    UPDATE transcode_queue
                    SET status = 'failed', error_message = ?
                    WHERE id = ?
                """, (str(e), queue_id))
                conn.execute("UPDATE media_files SET status = 'failed' WHERE id = ?", (media_id,))
                conn.commit()

queue_manager = QueueManager()
