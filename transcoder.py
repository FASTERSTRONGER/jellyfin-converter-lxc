import os
import shutil
from probe import (
    run_ffprobe, select_best_audio_for_downmix, select_primary_surround_track,
    get_stream_or_format_bitrate, is_commentary_or_auxiliary_audio, _resolve_binary
)

FFMPEG_BIN = _resolve_binary(
    "ffmpeg",
    ["/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "C:\\env\\ffmpeg\\ffmpeg.exe"]
)

def build_transcode_command(input_path, output_path, encoder="h264_amf", qp_crf="21", audio_bitrate="256k"):
    """
    Constructs the exact FFmpeg command array for transcoding.
    Output is written to a temporary file, preserving subtitles, attachments, and chapters.
    """
    data = run_ffprobe(input_path)
    if not data or "streams" not in data:
        raise ValueError(f"Could not read streams from {input_path}")
        
    streams = data["streams"]
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    
    main_v = None
    cover_art_indices = []
    for idx, v in enumerate(video_streams):
        disp = v.get("disposition", {})
        if disp.get("attached_pic") == 1 or v.get("codec_name") in ["mjpeg", "png"]:
            cover_art_indices.append(idx)
        elif main_v is None:
            main_v = v
            
    if main_v is None and video_streams:
        main_v = video_streams[0]
        
    # Check Video Stream & HDR properties
    v_codec = main_v.get("codec_name", "").lower()
    width = int(main_v.get("width", 0))
    height = int(main_v.get("height", 0))
    
    bits = main_v.get("bits_per_raw_sample")
    pix_fmt = main_v.get("pix_fmt", "").lower()
    if bits and str(bits).isdigit():
        bit_depth = int(bits)
    elif "10" in pix_fmt:
        bit_depth = 10
    elif "12" in pix_fmt:
        bit_depth = 12
    else:
        bit_depth = 8
        
    color_transfer = main_v.get("color_trc", "").lower()
    color_primaries = main_v.get("color_primaries", "").lower()
    is_hdr = (
        color_transfer in ["smpte2084", "arib-std-b67"] or
        color_primaries == "bt2020" or
        "hdr" in (main_v.get("tags", {}).get("title", "")).lower()
    )
    
    # Check video bitrate (e.g. Blu-Ray Remux > 12 Mbps)
    fmt = data.get("format", {})
    v_bitrate = get_stream_or_format_bitrate(main_v, fmt)
    
    # Determine if video track actually needs re-encoding
    video_needs_transcode = (
        v_codec != "h264" or
        bit_depth > 8 or
        width > 1920 or
        height > 1080 or
        is_hdr or
        v_bitrate > 12_000_000
    )
    
    # Fallback to libx264 on non-Windows if h264_amf is selected
    if os.name != "nt" and encoder == "h264_amf":
        encoder = "libx264"

    cmd = [FFMPEG_BIN, "-y", "-nostdin"]

    # Hardware/Filter device initialization
    if video_needs_transcode:
        if os.name == "nt" and encoder == "h264_amf":
            cmd.extend(["-hwaccel", "d3d11va"])
        elif os.name != "nt":
            # Bind Vulkan device (Mesa lavapipe on CPU-only LXC) for libplacebo
            cmd.extend(["-init_hw_device", "vulkan=vk:0", "-filter_hw_device", "vk"])
        
    cmd.extend(["-i", input_path])
    
    # 1. Map main video stream
    cmd.extend(["-map", "0:v:0"])
    
    if video_needs_transcode:
        # Video Filter: Jinc (EWA Lanczos) + Hybrid Anti-Ringing (Sigmoid + 0.8 Clamp) + madVR Tone-Mapping
        if is_hdr:
            vf = (
                "libplacebo=w='min(1920,iw)':h=-2:downscaler=ewa_lanczos:"
                "sigmoid=true:antiringing=0.8:tonemapping=spline:peak_detect=true:"
                "colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:format=yuv420p"
            )
        else:
            vf = "libplacebo=w='min(1920,iw)':h=-2:downscaler=ewa_lanczos:sigmoid=true:antiringing=0.8:format=yuv420p"
            
        cmd.extend(["-filter:v:0", vf])
        
        # Video Encoder settings with VBV rate control ceiling (prevents bit spikes on grainy/complex scenes)
        if encoder == "h264_amf":
            cmd.extend([
                "-c:v:0", "h264_amf",
                "-rc", "cqp",
                "-qp_p", str(qp_crf),
                "-qp_i", str(qp_crf),
                "-maxrate", "8000k",
                "-bufsize", "16000k"
            ])
        else:
            cmd.extend([
                "-c:v:0", "libx264",
                "-preset", "medium",
                "-crf", str(qp_crf),
                "-maxrate", "8000k",
                "-bufsize", "16000k"
            ])
    else:
        # Video is already H.264 8-bit <=1080p SDR: STREAM COPY! (Lossless, no re-encode)
        cmd.extend(["-c:v:0", "copy"])
        
    # 2. Audio Streams Mapping
    # Check if a main 2.0 Stereo AAC already exists (excluding commentary tracks)
    existing_stereo_aac_idx = None
    existing_stereo_other_idx = None
    for idx, a in enumerate(audio_streams):
        if is_commentary_or_auxiliary_audio(a):
            continue
        ch = int(a.get("channels", 2))
        cod = a.get("codec_name", "").lower()
        if ch == 2:
            if cod == "aac" and existing_stereo_aac_idx is None:
                existing_stereo_aac_idx = idx
            elif cod != "aac" and existing_stereo_other_idx is None:
                existing_stereo_other_idx = idx
                
    out_audio_track_idx = 0
    
    if existing_stereo_aac_idx is not None:
        # Case A1: Already has main Stereo AAC -> make it Track 0 (Default)
        cmd.extend(["-map", f"0:a:{existing_stereo_aac_idx}"])
        cmd.extend([f"-c:a:{out_audio_track_idx}", "copy"])
        cmd.extend([f"-disposition:a:{out_audio_track_idx}", "default"])
        out_audio_track_idx += 1
        
        # Preserve primary surround track if exists
        primary_surround_idx = select_primary_surround_track(audio_streams)
        preserved_indices = []
        if primary_surround_idx is not None and primary_surround_idx != existing_stereo_aac_idx:
            preserved_indices.append(primary_surround_idx)
            
        # Also preserve Indonesian audio track if present and distinct
        for idx, a in enumerate(audio_streams):
            if idx == existing_stereo_aac_idx or idx in preserved_indices or is_commentary_or_auxiliary_audio(a):
                continue
            lang = (a.get("tags", {}).get("language") or "").lower()
            if lang in ["ind", "id", "indonesian", "indo"]:
                preserved_indices.append(idx)
                
        for idx in preserved_indices:
            cmd.extend(["-map", f"0:a:{idx}"])
            cmd.extend([f"-c:a:{out_audio_track_idx}", "copy"])
            cmd.extend([f"-disposition:a:{out_audio_track_idx}", "0"])
            out_audio_track_idx += 1
                
    elif existing_stereo_other_idx is not None and not any(int(a.get("channels", 0)) > 2 for a in audio_streams if not is_commentary_or_auxiliary_audio(a)):
        # Case A2: Only stereo tracks, but not AAC -> convert first to AAC 256k
        cmd.extend(["-map", f"0:a:{existing_stereo_other_idx}"])
        cmd.extend([
            f"-c:a:{out_audio_track_idx}", "aac",
            f"-b:a:{out_audio_track_idx}", audio_bitrate,
            f"-disposition:a:{out_audio_track_idx}", "default"
        ])
        out_audio_track_idx += 1
        
        # If there is another distinct Indonesian audio track, preserve it
        for idx, a in enumerate(audio_streams):
            if idx == existing_stereo_other_idx or is_commentary_or_auxiliary_audio(a):
                continue
            lang = (a.get("tags", {}).get("language") or "").lower()
            if lang in ["ind", "id", "indonesian", "indo"]:
                cmd.extend(["-map", f"0:a:{idx}"])
                cmd.extend([f"-c:a:{out_audio_track_idx}", "copy"])
                cmd.extend([f"-disposition:a:{out_audio_track_idx}", "0"])
                out_audio_track_idx += 1
                
    else:
        # Case B: Surround tracks present -> select English or primary original for downmix to 2.0 Stereo AAC 256k
        best_a_idx = select_best_audio_for_downmix(audio_streams)
        best_stream = audio_streams[best_a_idx] if audio_streams else {}
        src_lang = best_stream.get("tags", {}).get("language", "und")
        
        # Track 0: Downmixed Stereo AAC 256k
        cmd.extend(["-map", f"0:a:{best_a_idx}"])
        cmd.extend([
            f"-c:a:{out_audio_track_idx}", "aac",
            f"-b:a:{out_audio_track_idx}", audio_bitrate,
            "-ac", "2",
            f"-metadata:s:a:{out_audio_track_idx}", 'title=Stereo Downmix (AAC 256k)',
            f"-metadata:s:a:{out_audio_track_idx}", f"language={src_lang}",
            f"-disposition:a:{out_audio_track_idx}", "default"
        ])
        out_audio_track_idx += 1
        
        # Track 1: Preserve ONLY primary surround track (English/Original)
        # and Indonesian track if present, pruning foreign dubs (Russian, Spanish, etc.)
        primary_surround_idx = select_primary_surround_track(audio_streams)
        preserved_indices = []
        if primary_surround_idx is not None:
            preserved_indices.append(primary_surround_idx)
            
        # Also preserve Indonesian audio track if present and distinct
        for idx, a in enumerate(audio_streams):
            if idx in preserved_indices or is_commentary_or_auxiliary_audio(a):
                continue
            lang = (a.get("tags", {}).get("language") or "").lower()
            if lang in ["ind", "id", "indonesian", "indo"]:
                preserved_indices.append(idx)
                
        for idx in preserved_indices:
            cmd.extend(["-map", f"0:a:{idx}"])
            cmd.extend([f"-c:a:{out_audio_track_idx}", "copy"])
            cmd.extend([f"-disposition:a:{out_audio_track_idx}", "0"])
            out_audio_track_idx += 1
            
    # 3. Subtitles: Copy all
    cmd.extend(["-map", "0:s?", "-c:s", "copy"])
    
    # 4. Cover Art / Attached Pictures: Copy all
    if cover_art_indices:
        out_v_idx = 1
        for in_v_idx in cover_art_indices:
            cmd.extend(["-map", f"0:v:{in_v_idx}"])
            # Index in output video streams: 1, 2, ...
            cmd.extend([f"-c:v:{out_v_idx}", "copy", f"-disposition:v:{out_v_idx}", "attached_pic"])
            out_v_idx += 1
            
    # 5. Fonts / Attachments: Copy all
    cmd.extend(["-map", "0:t?", "-c:t", "copy"])
    
    # 6. Chapters & Metadata
    cmd.extend(["-map_chapters", "0", "-map_metadata", "0"])
    
    # 7. Progress & Output
    cmd.extend(["-progress", "pipe:1", "-nostats", output_path])
    
    return cmd
