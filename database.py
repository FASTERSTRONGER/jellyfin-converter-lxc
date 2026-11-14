import sqlite3
import json
import os
import re
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_converter.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA cache_size = -64000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Media Files table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS media_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,
            relative_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            title TEXT DEFAULT '',
            filesize INTEGER DEFAULT 0,
            duration_sec REAL DEFAULT 0,
            video_codec TEXT DEFAULT '',
            video_profile TEXT DEFAULT '',
            pix_fmt TEXT DEFAULT '',
            bit_depth INTEGER DEFAULT 8,
            width INTEGER DEFAULT 0,
            height INTEGER DEFAULT 0,
            is_hdr INTEGER DEFAULT 0,
            color_transfer TEXT DEFAULT '',
            color_primaries TEXT DEFAULT '',
            audio_summary TEXT DEFAULT '',
            internal_subs TEXT DEFAULT '[]',
            external_subs TEXT DEFAULT '[]',
            subtitle_summary TEXT DEFAULT '',
            has_subtitles INTEGER DEFAULT 0,
            has_indonesian_sub INTEGER DEFAULT 0,
            has_external_sub INTEGER DEFAULT 0,
            needs_transcode INTEGER DEFAULT 0,
            transcode_reasons TEXT DEFAULT '[]',
            scanned_at TEXT,
            status TEXT DEFAULT 'scanned'
        )
        """)

        # Migration: Ensure new columns exist in existing DB
        existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(media_files)").fetchall()}
        cols_to_add = [
            ("title", "TEXT DEFAULT ''"),
            ("internal_subs", "TEXT DEFAULT '[]'"),
            ("external_subs", "TEXT DEFAULT '[]'"),
            ("subtitle_summary", "TEXT DEFAULT ''"),
            ("has_subtitles", "INTEGER DEFAULT 0"),
            ("has_indonesian_sub", "INTEGER DEFAULT 0"),
            ("has_external_sub", "INTEGER DEFAULT 0")
        ]
        for col_name, col_def in cols_to_add:
            if col_name not in existing_cols:
                cursor.execute(f"ALTER TABLE media_files ADD COLUMN {col_name} {col_def}")
        
        # 2. Transcode Queue table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcode_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER UNIQUE NOT NULL,
            output_path TEXT NOT NULL,
            encoder TEXT DEFAULT 'h264_amf',
            status TEXT DEFAULT 'pending',
            progress_percent REAL DEFAULT 0.0,
            current_fps REAL DEFAULT 0.0,
            current_speed TEXT DEFAULT '',
            eta_seconds INTEGER DEFAULT 0,
            started_at TEXT,
            finished_at TEXT,
            error_message TEXT DEFAULT '',
            worker_id INTEGER DEFAULT 0,
            FOREIGN KEY (media_id) REFERENCES media_files(id) ON DELETE CASCADE
        )
        """)
        
        # 3. Settings table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)
        
        # Default settings (OS-aware)
        is_windows = (os.name == "nt")
        default_settings = {
            "source_dir": "F:\\" if is_windows else "/mnt/gdrive/raw",
            "output_dir": "E:\\Jellyfin_Converted" if is_windows else "/mnt/gdrive/jellyfin",
            "staging_dir": "" if is_windows else "/var/cache/jellyfin-converter/staging",
            "encoder": "h264_amf" if is_windows else "libx264",
            "concurrent_workers": "2" if is_windows else "1",
            "crf_qp": "21",
            "audio_bitrate": "256k",
            "max_h264_bitrate_kbps": "12000",
            "queue_active": "false"
        }
        for k, v in default_settings.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
        conn.commit()

def get_setting(key, default=None):
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key, value):
    with get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def get_all_settings():
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

def upsert_media_file(data):
    with get_connection() as conn:
        now = datetime.now().isoformat()
        conn.execute("""
        INSERT INTO media_files (
            filepath, relative_path, filename, title, filesize, duration_sec,
            video_codec, video_profile, pix_fmt, bit_depth, width, height,
            is_hdr, color_transfer, color_primaries, audio_summary,
            internal_subs, external_subs, subtitle_summary,
            has_subtitles, has_indonesian_sub, has_external_sub,
            needs_transcode, transcode_reasons, scanned_at, status
        ) VALUES (
            :filepath, :relative_path, :filename, :title, :filesize, :duration_sec,
            :video_codec, :video_profile, :pix_fmt, :bit_depth, :width, :height,
            :is_hdr, :color_transfer, :color_primaries, :audio_summary,
            :internal_subs, :external_subs, :subtitle_summary,
            :has_subtitles, :has_indonesian_sub, :has_external_sub,
            :needs_transcode, :transcode_reasons, :scanned_at,
            COALESCE((SELECT status FROM media_files WHERE filepath = :filepath), 'scanned')
        )
        ON CONFLICT(filepath) DO UPDATE SET
            title = CASE WHEN excluded.title != '' THEN excluded.title ELSE media_files.title END,
            filesize = excluded.filesize,
            duration_sec = excluded.duration_sec,
            video_codec = excluded.video_codec,
            video_profile = excluded.video_profile,
            pix_fmt = excluded.pix_fmt,
            bit_depth = excluded.bit_depth,
            width = excluded.width,
            height = excluded.height,
            is_hdr = excluded.is_hdr,
            color_transfer = excluded.color_transfer,
            color_primaries = excluded.color_primaries,
            audio_summary = excluded.audio_summary,
            internal_subs = excluded.internal_subs,
            external_subs = excluded.external_subs,
            subtitle_summary = excluded.subtitle_summary,
            has_subtitles = excluded.has_subtitles,
            has_indonesian_sub = excluded.has_indonesian_sub,
            has_external_sub = excluded.has_external_sub,
            needs_transcode = excluded.needs_transcode,
            transcode_reasons = excluded.transcode_reasons,
            scanned_at = excluded.scanned_at
        """, {
            "title": "",
            "internal_subs": "[]",
            "external_subs": "[]",
            "subtitle_summary": "",
            "has_subtitles": 0,
            "has_indonesian_sub": 0,
            "has_external_sub": 0,
            **data,
            "scanned_at": now
        })
        conn.commit()

def update_file_subtitles(media_id, sub_data):
    """Updates subtitle information for a specific media file."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE media_files
            SET internal_subs = :internal_subs,
                external_subs = :external_subs,
                subtitle_summary = :subtitle_summary,
                has_subtitles = :has_subtitles,
                has_indonesian_sub = :has_indonesian_sub,
                has_external_sub = :has_external_sub
            WHERE id = :id
        """, {**sub_data, "id": media_id})
        conn.commit()

def get_media_files(filter_type="all", search="", limit=50, offset=0):
    with get_connection() as conn:
        conditions = []
        params = []
        
        # Parse multi-filter support (comma-separated or list)
        if isinstance(filter_type, str):
            filters = [f.strip() for f in filter_type.split(",") if f.strip()]
        else:
            filters = list(filter_type)
            
        if not filters:
            filters = ["all"]
            
        if len(filters) > 1 and "all" in filters:
            filters = [f for f in filters if f != "all"]
            
        for f in filters:
            if f == "needs_transcode":
                conditions.append("m.needs_transcode = 1 AND m.status != 'completed'")
            elif f == "audio_only":
                conditions.append("m.needs_transcode = 1 AND LOWER(m.video_codec) = 'h264' AND m.bit_depth <= 8 AND m.width <= 1920 AND m.height <= 1080 AND m.is_hdr = 0 AND m.status != 'completed'")
            elif f == "compatible":
                conditions.append("m.needs_transcode = 0")
            elif f == "queued":
                conditions.append("q.status = 'pending'")
            elif f == "completed":
                conditions.append("(m.status = 'completed' OR q.status = 'completed')")
            elif f == "not_completed":
                conditions.append("(m.status != 'completed' AND (q.status IS NULL OR q.status != 'completed'))")
            elif f == "converting":
                conditions.append("q.status = 'running'")
            elif f == "has_subs":
                conditions.append("m.has_subtitles = 1")
            elif f == "has_indo_subs":
                conditions.append("m.has_indonesian_sub = 1")
            elif f == "external_subs":
                conditions.append("m.has_external_sub = 1")
            elif f == "no_subs":
                conditions.append("m.has_subtitles = 0")
            elif f == "has_comm":
                conditions.append("m.audio_summary LIKE '%[COMM]%'")
            
        if search:
            conditions.append("(m.title LIKE ? OR m.filename LIKE ? OR m.filepath LIKE ? OR m.subtitle_summary LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])
            
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        
        query = f"""
        SELECT 
            m.*,
            q.status as queue_status,
            q.progress_percent,
            q.current_speed,
            q.current_fps,
            q.output_path
        FROM media_files m
        LEFT JOIN transcode_queue q ON m.id = q.media_id
        {where_clause}
        ORDER BY m.needs_transcode DESC, m.filename ASC
        LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        
        count_query = f"""
        SELECT COUNT(*) as total
        FROM media_files m
        LEFT JOIN transcode_queue q ON m.id = q.media_id
        {where_clause}
        """
        count_params = params[:-2]
        total = conn.execute(count_query, count_params).fetchone()["total"]
        
        return [dict(r) for r in rows], total

def get_stats():
    with get_connection() as conn:
        total_files = conn.execute("SELECT COUNT(*) as c FROM media_files").fetchone()["c"]
        needs_transcode = conn.execute("SELECT COUNT(*) as c FROM media_files WHERE needs_transcode = 1 AND status != 'completed'").fetchone()["c"]
        compatible = conn.execute("SELECT COUNT(*) as c FROM media_files WHERE needs_transcode = 0").fetchone()["c"]
        queued = conn.execute("SELECT COUNT(*) as c FROM transcode_queue WHERE status = 'pending'").fetchone()["c"]
        converting = conn.execute("SELECT COUNT(*) as c FROM transcode_queue WHERE status = 'running'").fetchone()["c"]
        completed = conn.execute("SELECT COUNT(*) as c FROM media_files WHERE status = 'completed'").fetchone()["c"]
        not_completed = conn.execute("SELECT COUNT(*) as c FROM media_files m LEFT JOIN transcode_queue q ON m.id = q.media_id WHERE (m.status != 'completed' AND (q.status IS NULL OR q.status != 'completed'))").fetchone()["c"]
        failed = conn.execute("SELECT COUNT(*) as c FROM transcode_queue WHERE status = 'failed'").fetchone()["c"]
        
        has_subtitles = conn.execute("SELECT COUNT(*) as c FROM media_files WHERE has_subtitles = 1").fetchone()["c"]
        has_indo_sub = conn.execute("SELECT COUNT(*) as c FROM media_files WHERE has_indonesian_sub = 1").fetchone()["c"]
        has_external_sub = conn.execute("SELECT COUNT(*) as c FROM media_files WHERE has_external_sub = 1").fetchone()["c"]
        no_subtitles = conn.execute("SELECT COUNT(*) as c FROM media_files WHERE has_subtitles = 0").fetchone()["c"]
        has_comm = conn.execute("SELECT COUNT(*) as c FROM media_files WHERE audio_summary LIKE '%[COMM]%'").fetchone()["c"]
        audio_only = conn.execute("SELECT COUNT(*) as c FROM media_files WHERE needs_transcode = 1 AND LOWER(video_codec) = 'h264' AND bit_depth <= 8 AND width <= 1920 AND height <= 1080 AND is_hdr = 0 AND status != 'completed'").fetchone()["c"]
        
        return {
            "total_files": total_files,
            "needs_transcode": needs_transcode,
            "audio_only": audio_only,
            "compatible": compatible,
            "queued": queued,
            "converting": converting,
            "completed": completed,
            "not_completed": not_completed,
            "failed": failed,
            "has_subtitles": has_subtitles,
            "has_indo_sub": has_indo_sub,
            "has_external_sub": has_external_sub,
            "no_subtitles": no_subtitles,
            "has_comm": has_comm
        }

def compute_output_prefix(media):
    """
    Menghitung prefix otomatis berdasarkan stream yang diubah:
    - Video diubah (bukan H.264, 10-bit, HDR, >1080p): 'x264_'
    - Audio diubah (tidak ada 2.0 AAC, perlu downmix atau transcode): 'aac_'
    - Keduanya diubah: 'x264_aac_'
    - Keduanya kompatibel: ''
    """
    v_codec = (media["video_codec"] or "").lower() if isinstance(media, (sqlite3.Row, dict)) else ""
    bit_depth = int(media["bit_depth"] or 8)
    width = int(media["width"] or 0)
    height = int(media["height"] or 0)
    is_hdr = int(media["is_hdr"] or 0)

    reasons = []
    tr_raw = media["transcode_reasons"]
    if tr_raw:
        try:
            reasons = json.loads(tr_raw) if isinstance(tr_raw, str) else tr_raw
        except Exception:
            reasons = []

    video_needs_transcode = (
        v_codec != "h264" or
        bit_depth > 8 or
        width > 1920 or
        height > 1080 or
        is_hdr == 1 or
        any("Video:" in r for r in reasons)
    )

    audio_needs_transcode = any("Audio:" in r for r in reasons)

    prefixes = []
    if video_needs_transcode:
        prefixes.append("x264")
    if audio_needs_transcode:
        prefixes.append("aac")

    if prefixes:
        return "_".join(prefixes) + "_"
    return ""

def add_to_queue(media_id, output_dir=None, encoder=None):
    with get_connection() as conn:
        media = conn.execute("SELECT * FROM media_files WHERE id = ?", (media_id,)).fetchone()
        if not media:
            return False
            
        if not output_dir:
            output_dir = get_setting("output_dir", "E:\\Jellyfin_Converted")
        if not encoder:
            encoder = get_setting("encoder", "h264_amf")
            
        # Target path keeps relative folder structure
        rel_dir = os.path.dirname(media["relative_path"])
        base_name, _ = os.path.splitext(media["filename"])
        # Bersihkan prefix yang sudah ada sebelumnya agar tidak duplikat (misal x264_x264_)
        clean_stem = re.sub(r'^(x264_|aac_|x264_aac_)+', '', base_name, flags=re.IGNORECASE)
        prefix = compute_output_prefix(media)
        dest_filename = f"{prefix}{clean_stem}.mkv"
        target_dir = os.path.join(output_dir, rel_dir) if rel_dir else output_dir
        target_path = os.path.join(target_dir, dest_filename)
        
        conn.execute("""
        INSERT INTO transcode_queue (media_id, output_path, encoder, status, progress_percent)
        VALUES (?, ?, ?, 'pending', 0.0)
        ON CONFLICT(media_id) DO UPDATE SET
            output_path = excluded.output_path,
            encoder = excluded.encoder,
            status = 'pending',
            progress_percent = 0.0,
            error_message = ''
        """, (media_id, target_path, encoder))
        
        conn.execute("UPDATE media_files SET status = 'queued' WHERE id = ?", (media_id,))
        conn.commit()
        return True

def add_all_needing_transcode(output_dir=None, encoder=None):
    with get_connection() as conn:
        rows = conn.execute("SELECT id FROM media_files WHERE needs_transcode = 1 AND status != 'completed'").fetchall()
        count = 0
        for r in rows:
            if add_to_queue(r["id"], output_dir, encoder):
                count += 1
        return count

def mark_as_completed(media_id):
    with get_connection() as conn:
        now = datetime.now().isoformat()
        conn.execute("UPDATE media_files SET status = 'completed' WHERE id = ?", (media_id,))
        conn.execute("""
            INSERT INTO transcode_queue (media_id, output_path, status, progress_percent, finished_at)
            VALUES (?, 'MANUAL_COMPLETED', 'completed', 100.0, ?)
            ON CONFLICT(media_id) DO UPDATE SET
                status = 'completed',
                progress_percent = 100.0,
                finished_at = ?
        """, (media_id, now, now))
        conn.commit()
        return True

def reset_file_status(media_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM transcode_queue WHERE media_id = ?", (media_id,))
        conn.execute("UPDATE media_files SET status = 'scanned' WHERE id = ?", (media_id,))
        conn.commit()
        return True

def get_queue(status=None):
    with get_connection() as conn:
        if status:
            rows = conn.execute("""
                SELECT q.*, m.filepath, m.filename, m.title, m.duration_sec, m.video_codec, m.width, m.height, m.is_hdr
                FROM transcode_queue q
                JOIN media_files m ON q.media_id = m.id
                WHERE q.status = ?
                ORDER BY q.id ASC
            """, (status,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT q.*, m.filepath, m.filename, m.title, m.duration_sec, m.video_codec, m.width, m.height, m.is_hdr
                FROM transcode_queue q
                JOIN media_files m ON q.media_id = m.id
                ORDER BY 
                    CASE q.status 
                        WHEN 'running' THEN 1 
                        WHEN 'pending' THEN 2 
                        WHEN 'paused' THEN 3 
                        WHEN 'failed' THEN 4 
                        ELSE 5 
                    END, q.id ASC
            """).fetchall()
        return [dict(r) for r in rows]

def remove_from_queue(queue_id):
    with get_connection() as conn:
        row = conn.execute("SELECT media_id FROM transcode_queue WHERE id = ?", (queue_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM transcode_queue WHERE id = ?", (queue_id,))
            conn.execute("UPDATE media_files SET status = 'scanned' WHERE id = ?", (row["media_id"],))
            conn.commit()
            return True
        return False

def clear_completed_queue():
    with get_connection() as conn:
        conn.execute("DELETE FROM transcode_queue WHERE status = 'completed'")
        conn.commit()

def reset_running_to_pending():
    """Recovers tasks interrupted by shutdown or crash."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE transcode_queue 
            SET status = 'pending', progress_percent = 0.0, current_speed = '', current_fps = 0.0 
            WHERE status = 'running'
        """)
        conn.commit()
