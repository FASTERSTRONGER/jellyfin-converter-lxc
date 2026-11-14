import json
import subprocess
import shutil
import os
import re

def _resolve_binary(name, fallbacks):
    found = shutil.which(name)
    if found:
        return found
    for path in fallbacks:
        if os.path.exists(path):
            return path
    return name

FFPROBE_BIN = _resolve_binary(
    "ffprobe",
    ["/usr/local/bin/ffprobe", "/usr/bin/ffprobe", "C:\\env\\ffmpeg\\ffprobe.exe"]
)

SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"}

ID_STOPWORDS = {
    "yang", "dan", "di", "ke", "dari", "ini", "itu", "untuk", "bisa", "kamu",
    "aku", "kita", "tidak", "ada", "apa", "mereka", "sudah", "akan", "pada",
    "juga", "saya", "tapi", "dia", "oleh", "dalam"
}

EN_STOPWORDS = {
    "the", "and", "to", "of", "you", "that", "was", "with", "have", "this",
    "what", "here", "just", "know", "about", "there", "from", "they", "will",
    "would", "them", "some"
}

def is_commentary_or_auxiliary_audio(stream):
    """
    Returns True if an audio stream is a commentary, descriptive, hearing impaired,
    or other auxiliary track that should NOT be considered the main feature audio.
    """
    disp = stream.get("disposition", {})
    if any(disp.get(k, 0) == 1 for k in ["comment", "hearing_impaired", "visual_impaired", "descriptions"]):
        return True
        
    tags = stream.get("tags", {})
    title = (tags.get("title") or tags.get("TITLE") or "").lower()
    
    # Check title keywords
    commentary_patterns = [
        r"\bcommentary\b",
        r"\bkomentar\b",
        r"\bdirector\b",
        r"\bproducer\b",
        r"\bwriter\b",
        r"\bcast\b",
        r"\bcrew\b",
        r"\bactor\b",
        r"\bfilmmaker\b",
        r"\baudiodesc\b",
        r"\baudio description\b",
        r"\bdescription\b",
        r"\bdescriptive\b",
        r"\bpip\b",
        r"\bvisual impaired\b",
        r"\bhearing impaired\b",
        r"\bsdh\b"
    ]
    for pat in commentary_patterns:
        if re.search(pat, title):
            return True
            
    return False

def detect_subtitle_language(filepath):
    """
    Detects subtitle language using filename indicators and text content frequency.
    Returns standard 3-letter codes: 'IND', 'ENG', 'JPN', 'KOR', 'ARA', etc.
    """
    fn = os.path.basename(filepath).lower()
    stem = os.path.splitext(fn)[0]
    parts = re.split(r'[\.\-_\s]', stem)
    
    # 1. Filename tags
    for p in parts:
        if p in ["id", "ind", "indonesian", "indo", "bahasa", "in", "id-id", "indonesia"]:
            return "IND"
        if p in ["en", "eng", "english", "en-us", "en-gb"]:
            return "ENG"
        if p in ["ja", "jpn", "japanese"]:
            return "JPN"
        if p in ["ko", "kor", "korean"]:
            return "KOR"
        if p in ["zh", "chi", "chinese"]:
            return "CHI"
        if p in ["es", "spa", "spanish"]:
            return "SPA"
        if p in ["fr", "fre", "fra", "french"]:
            return "FRE"
        if p in ["ar", "ara", "arabic"]:
            return "ARA"
            
    # 2. Text Content Heuristic (for text-based subtitles)
    ext = os.path.splitext(fn)[1].lower()
    if ext in [".srt", ".ass", ".ssa", ".vtt"]:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(6000).lower()
            words = set(re.findall(r'[a-z]+', content))
            id_score = len(words.intersection(ID_STOPWORDS))
            en_score = len(words.intersection(EN_STOPWORDS))
            
            if id_score > en_score and id_score >= 2:
                return "IND"
            elif en_score > id_score and en_score >= 2:
                return "ENG"
        except Exception:
            pass
            
    return "UND"

def is_subtitle_for_video(sub_filename, video_filename, all_video_stems):
    """
    Checks if an external subtitle file belongs to the target video.
    Handles single-video folders, multiple episodes (e.g. S01E01), and prefixed names.
    """
    v_stem = os.path.splitext(video_filename)[0].lower()
    s_stem = os.path.splitext(sub_filename)[0].lower()
    
    if len(all_video_stems) <= 1:
        return True
        
    if s_stem == v_stem or s_stem.startswith(v_stem):
        return True
        
    # Check if another video stem is a closer match
    for other_stem in all_video_stems:
        if other_stem != v_stem and s_stem.startswith(other_stem):
            return False
            
    # Check Season/Episode tag match (e.g. S01E01)
    ep_v = re.search(r's\d{1,2}e\d{1,2}', v_stem)
    ep_s = re.search(r's\d{1,2}e\d{1,2}', s_stem)
    if ep_v and ep_s:
        return ep_v.group(0) == ep_s.group(0)
        
    return False

def find_external_subtitles(video_path):
    """
    Scans the directory of video_path and companion subfolders (Subs, Subtitles)
    for external subtitle files matching this video.
    """
    v_dir = os.path.dirname(video_path)
    v_file = os.path.basename(video_path)
    
    video_exts = {".mkv", ".mp4", ".avi", ".webm", ".ts", ".m4v"}
    all_video_stems = []
    try:
        for item in os.listdir(v_dir):
            if os.path.splitext(item)[1].lower() in video_exts:
                all_video_stems.append(os.path.splitext(item)[0].lower())
    except Exception:
        pass
        
    search_dirs = [v_dir]
    for subfolder in ["Subs", "subtitles", "Subtitles", "sub"]:
        s_path = os.path.join(v_dir, subfolder)
        if os.path.isdir(s_path):
            search_dirs.append(s_path)
            
    results = []
    seen_paths = set()
    
    for s_dir in search_dirs:
        try:
            for item in os.listdir(s_dir):
                ext = os.path.splitext(item)[1].lower()
                if ext in SUBTITLE_EXTENSIONS:
                    full_p = os.path.join(s_dir, item)
                    if full_p in seen_paths:
                        continue
                        
                    if is_subtitle_for_video(item, v_file, all_video_stems):
                        seen_paths.add(full_p)
                        lang = detect_subtitle_language(full_p)
                        is_forced = "forced" in item.lower()
                        sz = os.path.getsize(full_p) if os.path.exists(full_p) else 0
                        results.append({
                            "filename": item,
                            "path": full_p,
                            "ext": ext[1:].lower(),
                            "language": lang,
                            "is_forced": is_forced,
                            "size": sz
                        })
        except Exception:
            pass
            
    return results

def summarize_subtitles(internal_subs, external_subs):
    """Formats a concise human-readable subtitle summary."""
    parts = []
    if internal_subs:
        # Group identical codec/lang counts
        counts = {}
        for s in internal_subs:
            lang = s.get("language", "und").upper()
            codec = s.get("codec", "").upper()
            codec_label = "SRT" if "SUBRIP" in codec else ("PGS" if "PGS" in codec else ("ASS" if "ASS" in codec or "SSA" in codec else codec))
            key = f"{lang}:{codec_label}"
            counts[key] = counts.get(key, 0) + 1
            
        int_items = []
        for k, c in counts.items():
            if c > 1:
                int_items.append(f"{k} (x{c})")
            else:
                int_items.append(k)
        parts.append("Internal: " + ", ".join(int_items))
        
    if external_subs:
        ext_items = []
        for s in external_subs:
            fn = s.get("filename", "")
            lang = s.get("language", "UND").upper()
            ext_items.append(f"{fn} [{lang}]")
        parts.append("Eksternal: " + ", ".join(ext_items))
        
    return " | ".join(parts) if parts else "Tanpa Subtitle"

def probe_subtitles_fast(filepath):
    """
    Fast standalone inspector for internal & external subtitles (under ~50-80ms).
    """
    external_subs = find_external_subtitles(filepath)
    internal_subs = []
    
    cmd = [
        FFPROBE_BIN,
        "-v", "error",
        "-select_streams", "s",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title:stream_disposition=default,forced,hearing_impaired",
        "-of", "json",
        filepath
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=8)
        if res.returncode == 0 and res.stdout:
            parsed = json.loads(res.stdout)
            for s in parsed.get("streams", []):
                tags = s.get("tags", {})
                disp = s.get("disposition", {})
                internal_subs.append({
                    "index": s.get("index"),
                    "codec": s.get("codec_name", "").lower(),
                    "language": tags.get("language", "und").lower(),
                    "title": tags.get("title", ""),
                    "default": disp.get("default", 0) == 1,
                    "forced": disp.get("forced", 0) == 1,
                    "sdh": disp.get("hearing_impaired", 0) == 1
                })
    except Exception:
        pass
        
    summary = summarize_subtitles(internal_subs, external_subs)
    has_subs = 1 if (internal_subs or external_subs) else 0
    has_indo = 0
    for s in internal_subs:
        if s.get("language") in ["ind", "id", "indonesian", "indo"]:
            has_indo = 1
            break
    if not has_indo:
        for s in external_subs:
            if s.get("language") in ["IND", "ind", "id"]:
                has_indo = 1
                break
                
    return {
        "internal_subs": json.dumps(internal_subs),
        "external_subs": json.dumps(external_subs),
        "subtitle_summary": summary,
        "has_subtitles": has_subs,
        "has_indonesian_sub": has_indo,
        "has_external_sub": 1 if external_subs else 0
    }

def run_ffprobe(filepath):
    """Executes ffprobe and returns JSON dictionary of format and streams."""
    if not os.path.exists(filepath):
        return None
    cmd = [
        FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        filepath
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0 and res.stdout:
            return json.loads(res.stdout)
    except Exception as e:
        print(f"Error probing {filepath}: {e}")
    return None

def get_stream_or_format_bitrate(video_stream, format_data):
    """Returns the effective video bitrate in bps, checking stream bit_rate, BPS tags, format bit_rate, or size/duration."""
    if not video_stream:
        return 0
    br = video_stream.get("bit_rate")
    if br and str(br).isdigit() and int(br) > 0:
        return int(br)
    tags = video_stream.get("tags", {})
    bps = tags.get("BPS") or tags.get("BPS-eng") or tags.get("bps")
    if bps and str(bps).isdigit() and int(bps) > 0:
        return int(bps)
    if format_data:
        f_br = format_data.get("bit_rate")
        if f_br and str(f_br).isdigit() and int(f_br) > 0:
            return int(f_br)
        sz = int(format_data.get("size", 0))
        dur = float(format_data.get("duration", 0.0))
        if sz > 0 and dur > 0:
            return int((sz * 8) / dur)
    return 0

def analyze_media(filepath, relative_path=""):
    """
    Deep-probes media file to determine video/audio details and whether it needs conversion.
    """
    data = run_ffprobe(filepath)
    if not data or "streams" not in data:
        return None
        
    streams = data["streams"]
    fmt = data.get("format", {})
    fmt_tags = fmt.get("tags", {})
    
    # 0. Metadata Title
    title = (fmt_tags.get("title") or fmt_tags.get("TITLE") or fmt_tags.get("Title") or "").strip()
    if not title:
        for s in streams:
            if s.get("codec_type") == "video":
                st = (s.get("tags", {}).get("title") or s.get("tags", {}).get("TITLE") or "").strip()
                if st and not any(k in st.lower() for k in ["video", "track", "1080p", "2160p", "default"]):
                    title = st
                    break
    
    filesize = int(fmt.get("size", 0)) or os.path.getsize(filepath)
    duration_sec = float(fmt.get("duration", 0.0))
    
    # 1. Inspect Video Streams
    video_stream = None
    attached_pics = []
    for s in streams:
        if s.get("codec_type") == "video":
            disp = s.get("disposition", {})
            if disp.get("attached_pic") == 1 or s.get("codec_name") in ["mjpeg", "png"]:
                attached_pics.append(s)
            elif not video_stream:
                video_stream = s
                
    if not video_stream:
        return None
        
    v_codec = video_stream.get("codec_name", "").lower()
    v_profile = video_stream.get("profile", "")
    pix_fmt = video_stream.get("pix_fmt", "").lower()
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    
    # Inferred bit depth
    bits = video_stream.get("bits_per_raw_sample")
    if bits and bits.isdigit():
        bit_depth = int(bits)
    elif "10" in pix_fmt:
        bit_depth = 10
    elif "12" in pix_fmt:
        bit_depth = 12
    else:
        bit_depth = 8
        
    color_transfer = video_stream.get("color_trc", "").lower()
    color_primaries = video_stream.get("color_primaries", "").lower()
    color_space = video_stream.get("color_space", "").lower()
    
    is_hdr = (
        color_transfer in ["smpte2084", "arib-std-b67"] or
        color_primaries == "bt2020" or
        "hdr" in (video_stream.get("tags", {}).get("title", "")).lower()
    )
    
    # 2. Inspect Audio Streams
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    audio_summaries = []
    has_main_stereo_aac = False
    has_main_stereo_other = False
    has_commentary_stereo = False
    max_channels = 0
    
    for a in audio_streams:
        channels = int(a.get("channels", 2))
        max_channels = max(max_channels, channels)
        a_codec = a.get("codec_name", "").lower()
        tags = a.get("tags", {})
        lang = tags.get("language", "und").lower()
        title = tags.get("title", "")
        
        is_comm = is_commentary_or_auxiliary_audio(a)
        comm_tag = " [COMM]" if is_comm else ""
        audio_summaries.append(f"{lang.upper()}:{a_codec.upper()} {channels}ch{comm_tag}")
        
        if is_comm:
            if channels == 2:
                has_commentary_stereo = True
        else:
            if channels == 2:
                if a_codec == "aac":
                    has_main_stereo_aac = True
                else:
                    has_main_stereo_other = True
                
    # 3. Inspect Subtitle Streams
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    internal_subs = []
    for s in sub_streams:
        tags = s.get("tags", {})
        disp = s.get("disposition", {})
        internal_subs.append({
            "index": s.get("index"),
            "codec": s.get("codec_name", "").lower(),
            "language": tags.get("language", "und").lower(),
            "title": tags.get("title", ""),
            "default": disp.get("default", 0) == 1,
            "forced": disp.get("forced", 0) == 1,
            "sdh": disp.get("hearing_impaired", 0) == 1
        })
        
    # Inspect External Subtitles
    external_subs = find_external_subtitles(filepath)
    sub_summary = summarize_subtitles(internal_subs, external_subs)
    
    has_subs = 1 if (internal_subs or external_subs) else 0
    has_indo = 0
    for s in internal_subs:
        if s.get("language") in ["ind", "id", "indonesian", "indo"]:
            has_indo = 1
            break
    if not has_indo:
        for s in external_subs:
            if s.get("language") in ["IND", "ind", "id"]:
                has_indo = 1
                break

    # 4. Determine Transcode Reasons
    reasons = []
    
    # Video criteria
    if v_codec != "h264":
        reasons.append(f"Video: Bukan H.264 ({v_codec.upper()})")
    if bit_depth > 8:
        reasons.append(f"Video: {bit_depth}-bit (Jellyfin i5 butuh 8-bit)")
    if width > 1920 or height > 1080:
        reasons.append(f"Video: Resolusi > 1080p ({width}x{height})")
    if is_hdr:
        reasons.append("Video: HDR (Perlu tone-mapping ke SDR 100 nits)")
    if v_codec == "h264" and bit_depth <= 8 and width <= 1920 and height <= 1080 and not is_hdr:
        v_bitrate = get_stream_or_format_bitrate(video_stream, fmt)
        if v_bitrate > 12_000_000:
            reasons.append(f"Video: Bitrate sangat tinggi ({v_bitrate // 1000} kbps > 12 Mbps - perlu kompresi)")
        
    # Audio criteria
    if not has_main_stereo_aac:
        if has_main_stereo_other:
            reasons.append("Audio: Ada track stereo utama tetapi bukan AAC")
        elif has_commentary_stereo and not any(int(a.get("channels", 2)) == 2 for a in audio_streams if not is_commentary_or_auxiliary_audio(a)):
            reasons.append(f"Audio: Track stereo yang ada hanya Commentary ({max_channels}ch surround - perlu downmix ke 2.0 AAC)")
        else:
            reasons.append(f"Audio: Tidak ada stereo utama ({max_channels}ch surround - perlu downmix ke 2.0 AAC)")
            
    needs_transcode = 1 if len(reasons) > 0 else 0
    
    # 5. Audio selection for downmix heuristic
    best_audio_index = select_best_audio_for_downmix(audio_streams)
    
    filename = os.path.basename(filepath)
    if not relative_path:
        relative_path = filename

    return {
        "filepath": filepath,
        "relative_path": relative_path,
        "filename": filename,
        "title": title,
        "filesize": filesize,
        "duration_sec": duration_sec,
        "video_codec": v_codec,
        "video_profile": v_profile,
        "pix_fmt": pix_fmt,
        "bit_depth": bit_depth,
        "width": width,
        "height": height,
        "is_hdr": 1 if is_hdr else 0,
        "color_transfer": color_transfer,
        "color_primaries": color_primaries,
        "audio_summary": ", ".join(audio_summaries),
        "internal_subs": json.dumps(internal_subs),
        "external_subs": json.dumps(external_subs),
        "subtitle_summary": sub_summary,
        "has_subtitles": has_subs,
        "has_indonesian_sub": has_indo,
        "has_external_sub": 1 if external_subs else 0,
        "needs_transcode": needs_transcode,
        "transcode_reasons": json.dumps(reasons),
        "best_audio_stream_index": best_audio_index,
        "raw_streams": streams
    }

def select_best_audio_for_downmix(audio_streams):
    """
    Selects the primary non-dubbed, English or original audio track for stereo downmix.
    Returns relative index in audio_streams list (0-based).
    """
    if not audio_streams:
        return 0
        
    candidates = []
    for idx, a in enumerate(audio_streams):
        tags = a.get("tags", {})
        title = tags.get("title", "").lower()
        lang = tags.get("language", "und").lower()
        disp = a.get("disposition", {})
        
        # Penalize commentary, hearing impaired, dubs
        is_commentary = is_commentary_or_auxiliary_audio(a)
        is_dub = disp.get("dub", 0) == 1 or "dub" in title or "dubbed" in title
        
        if is_commentary:
            continue
            
        score = 0
        if not is_dub:
            score += 50
        if lang in ["eng", "en", "english"]:
            score += 100
        if disp.get("original", 0) == 1:
            score += 80
        if disp.get("default", 0) == 1:
            score += 30
            
        # Prefer higher channel count for downmix source if multiple choices
        channels = int(a.get("channels", 2))
        score += channels
        
        candidates.append((score, idx))
        
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
        
    return 0

def select_primary_surround_track(audio_streams):
    """
    Selects the single best non-dubbed surround (>= 3 channels) audio track to preserve
    alongside the 2.0 stereo AAC downmix.
    Returns relative index in audio_streams list (0-based), or None if no surround tracks exist.
    """
    if not audio_streams:
        return None
        
    surround_candidates = []
    for idx, a in enumerate(audio_streams):
        channels = int(a.get("channels", 2))
        if channels <= 2:
            continue
        if is_commentary_or_auxiliary_audio(a):
            continue
            
        tags = a.get("tags", {})
        title = (tags.get("title") or tags.get("TITLE") or "").lower()
        lang = (tags.get("language") or tags.get("LANGUAGE") or "und").lower()
        disp = a.get("disposition", {})
        
        is_dub = (
            disp.get("dub", 0) == 1 or
            "dub" in title or
            "dubbed" in title or
            "voiceover" in title
        )
        
        score = 0
        if not is_dub:
            score += 100
            
        # Language preference: Indonesian > English > Original > Undefined > Others
        if lang in ["ind", "id", "indonesian", "indo"]:
            score += 200
        elif lang in ["eng", "en", "english"]:
            score += 150
        elif disp.get("original", 0) == 1:
            score += 120
        elif lang in ["und", ""]:
            score += 40
        else:
            # Foreign dubbing (e.g. rus, fre, ger, spa on English/foreign release)
            score -= 50
            
        if disp.get("default", 0) == 1:
            score += 20
            
        # Prefer higher channel count (8ch > 6ch)
        score += channels
        
        surround_candidates.append((score, idx))
        
    if surround_candidates:
        surround_candidates.sort(key=lambda x: x[0], reverse=True)
        if surround_candidates[0][0] > 0:
            return surround_candidates[0][1]
            
    return None

