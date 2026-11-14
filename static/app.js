let activeFilters = new Set(["all"]);
let currentSearch = "";
let currentPage = 0;
const pageSize = 50;

const filterLabels = {
  all: "Semua File",
  needs_transcode: "Wajib Konversi",
  audio_only: "🎧 Transcode Audio Saja",
  compatible: "Sudah Kompatibel",
  has_indo_subs: "🇮🇩 Ada Sub Indo",
  external_subs: "📄 Sub Eksternal",
  no_subs: "🚫 Tanpa Subtitle",
  has_comm: "🎙️ Ada Komentar",
  queued: "Dalam Antrean",
  not_completed: "⏳ Belum Selesai",
  completed: "✔ Selesai"
};

document.addEventListener("DOMContentLoaded", () => {
  initEventListeners();
  loadSystemInfo();
  pollStats();
  loadFiles();
  loadQueue();

  // Polling loop every 1500ms
  setInterval(() => {
    pollStats();
    loadQueue();
  }, 1500);
});

function initEventListeners() {
  // Scanner controls
  document.getElementById("btnStartScan").addEventListener("click", startScan);
  document.getElementById("btnStopScan").addEventListener("click", stopScan);
  const btnScanSubs = document.getElementById("btnScanSubs");
  if (btnScanSubs) {
    btnScanSubs.addEventListener("click", startSubtitleScan);
  }

  // Queue controls
  document.getElementById("btnStartQueue").addEventListener("click", startQueue);
  document.getElementById("btnPauseQueue").addEventListener("click", pauseQueue);
  document.getElementById("btnClearCompleted").addEventListener("click", clearCompletedQueue);
  document.getElementById("btnAddAllTranscode").addEventListener("click", addAllToQueue);
  document.getElementById("btnSaveSettings").addEventListener("click", saveSettings);

  // Multi-Filter tabs
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      const clicked = e.currentTarget.getAttribute("data-filter");
      toggleFilter(clicked);
    });
  });

  const btnReset = document.getElementById("btnResetFilters");
  if (btnReset) {
    btnReset.addEventListener("click", () => {
      activeFilters.clear();
      activeFilters.add("all");
      updateFilterUI();
      currentPage = 0;
      loadFiles();
    });
  }

  // Search input with debounce
  let searchTimeout;
  document.getElementById("searchBox").addEventListener("input", (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      currentSearch = e.target.value.trim();
      currentPage = 0;
      loadFiles();
    }, 300);
  });

  // Pagination
  document.getElementById("btnPrevPage").addEventListener("click", () => {
    if (currentPage > 0) {
      currentPage--;
      loadFiles();
    }
  });
  document.getElementById("btnNextPage").addEventListener("click", () => {
    currentPage++;
    loadFiles();
  });
}

async function loadSystemInfo() {
  try {
    const res = await fetch("/api/system_info");
    const data = await res.json();
    if (data.gpu) document.getElementById("gpuName").textContent = data.gpu;
    if (data.output_drive_free_gb !== undefined) {
      document.getElementById("freeDiskSpace").textContent = `${data.output_drive_free_gb} GB Free`;
    }
  } catch (e) {
    console.error("Failed to load system info", e);
  }
}

let lastStatsSnapshot = null;
let lastScannerState = null;

async function pollStats() {
  try {
    const res = await fetch("/api/stats");
    const data = await res.json();
    const stats = data.stats;
    const scanner = data.scanner;
    const isQueueActive = data.queue_active;

    // Auto-refresh file library table if queue state or scanner state changes
    if (lastStatsSnapshot !== null) {
      const statsChanged = (
        stats.completed !== lastStatsSnapshot.completed ||
        stats.converting !== lastStatsSnapshot.converting ||
        stats.queued !== lastStatsSnapshot.queued ||
        stats.failed !== lastStatsSnapshot.failed ||
        stats.total_files !== lastStatsSnapshot.total_files
      );
      if (statsChanged) {
        loadFiles();
      }
    }
    lastStatsSnapshot = {
      completed: stats.completed,
      converting: stats.converting,
      queued: stats.queued,
      failed: stats.failed,
      total_files: stats.total_files
    };

    // Auto-refresh if scanning just finished
    if (lastScannerState !== null && lastScannerState.is_scanning && !scanner.is_scanning) {
      loadFiles();
    }
    lastScannerState = { is_scanning: scanner.is_scanning };

    // Update Top Metrics
    document.getElementById("statTotal").textContent = stats.total_files.toLocaleString();
    document.getElementById("statNeedsTranscode").textContent = stats.needs_transcode.toLocaleString();
    document.getElementById("statCompatible").textContent = stats.compatible.toLocaleString();
    document.getElementById("statQueueCount").textContent = (stats.queued + stats.converting).toLocaleString();
    document.getElementById("statQueueConverting").textContent = `${stats.converting} sedang diproses`;
    
    if (document.getElementById("statIndoSubs")) {
      document.getElementById("statIndoSubs").textContent = (stats.has_indo_sub || 0).toLocaleString();
      document.getElementById("statExtSubs").textContent = `${(stats.has_external_sub || 0).toLocaleString()} file eksternal`;
    }

    // Queue button states
    const btnStart = document.getElementById("btnStartQueue");
    const btnPause = document.getElementById("btnPauseQueue");
    const queueBadge = document.getElementById("queueBadge");

    if (isQueueActive) {
      btnStart.style.display = "none";
      btnPause.style.display = "inline-flex";
      queueBadge.className = "status-pill status-running";
      queueBadge.textContent = "Running";
    } else {
      btnStart.style.display = "inline-flex";
      btnPause.style.display = "none";
      queueBadge.className = "status-pill status-idle";
      queueBadge.textContent = "Idle";
    }

    // Scanner UI update
    const scanContainer = document.getElementById("scanProgressBarContainer");
    const btnStartScan = document.getElementById("btnStartScan");
    const btnStopScan = document.getElementById("btnStopScan");

    if (scanner.is_scanning) {
      scanContainer.style.display = "block";
      btnStartScan.style.display = "none";
      btnStopScan.style.display = "inline-flex";

      const pct = scanner.total_found > 0 ? Math.round((scanner.scanned_count / scanner.total_found) * 100) : 0;
      document.getElementById("scanProgressFill").style.width = `${pct}%`;
      document.getElementById("scanCountText").textContent = `${scanner.scanned_count} / ${scanner.total_found} file (${pct}%)`;
      document.getElementById("scanStatusText").textContent = "Memindai pustaka media...";
      document.getElementById("scanCurrentFile").textContent = scanner.current_file;
    } else {
      btnStartScan.style.display = "inline-flex";
      btnStopScan.style.display = "none";
      if (scanner.current_file && scanner.current_file.includes("selesai")) {
        document.getElementById("scanStatusText").textContent = scanner.current_file;
        document.getElementById("scanProgressFill").style.width = "100%";
      }
    }

    // Subtitle Scanner UI update
    const subContainer = document.getElementById("subScanProgressBarContainer");
    const btnScanSubs = document.getElementById("btnScanSubs");
    const subScan = scanner.sub_scan;
    if (subScan && subScan.is_scanning) {
      subContainer.style.display = "block";
      if (btnScanSubs) btnScanSubs.disabled = true;
      const subPct = subScan.total > 0 ? Math.round((subScan.count / subScan.total) * 100) : 0;
      document.getElementById("subScanProgressFill").style.width = `${subPct}%`;
      document.getElementById("subScanCountText").textContent = `${subScan.count} / ${subScan.total} file (${subPct}%)`;
      document.getElementById("subScanStatusText").textContent = "Memeriksa subtitle internal & eksternal...";
      document.getElementById("subScanCurrentFile").textContent = subScan.current_file;
    } else {
      if (btnScanSubs) btnScanSubs.disabled = false;
      if (subScan && subScan.current_file && subScan.current_file.includes("selesai")) {
        subContainer.style.display = "block";
        document.getElementById("subScanStatusText").textContent = subScan.current_file;
        document.getElementById("subScanProgressFill").style.width = "100%";
        setTimeout(() => {
          if (!scanner.sub_scan?.is_scanning) subContainer.style.display = "none";
        }, 5000);
      }
    }

    // Settings sync (only on first load)
    if (!window.settingsLoaded && data.settings) {
      window.settingsLoaded = true;
      if (data.settings.source_dir) {
        document.getElementById("settingSourceDir").value = data.settings.source_dir;
        updateSourceDirLabels(data.settings.source_dir);
      }
      if (data.settings.output_dir) document.getElementById("settingOutputDir").value = data.settings.output_dir;
      if (data.settings.staging_dir !== undefined) document.getElementById("settingStagingDir").value = data.settings.staging_dir;
      if (data.settings.encoder) document.getElementById("settingEncoder").value = data.settings.encoder;
      if (data.settings.concurrent_workers) document.getElementById("settingWorkers").value = data.settings.concurrent_workers;
    }

  } catch (e) {
    console.error("Failed to poll stats", e);
  }
}

function toggleFilter(clicked) {
  if (clicked === "all") {
    activeFilters.clear();
    activeFilters.add("all");
  } else {
    activeFilters.delete("all");

    if (activeFilters.has(clicked)) {
      activeFilters.delete(clicked);
    } else {
      // Cleanly handle mutually exclusive pairs
      if (clicked === "audio_only") {
        activeFilters.delete("compatible");
        activeFilters.delete("needs_transcode");
      }
      if (clicked === "needs_transcode") {
        activeFilters.delete("compatible");
        activeFilters.delete("audio_only");
      }
      if (clicked === "compatible") {
        activeFilters.delete("needs_transcode");
        activeFilters.delete("audio_only");
      }
      if (clicked === "no_subs") {
        activeFilters.delete("has_indo_subs");
        activeFilters.delete("external_subs");
        activeFilters.delete("has_subs");
      }
      if (clicked === "has_indo_subs" || clicked === "external_subs" || clicked === "has_subs") {
        activeFilters.delete("no_subs");
      }
      if (clicked === "queued") activeFilters.delete("completed");
      if (clicked === "completed") {
        activeFilters.delete("queued");
        activeFilters.delete("not_completed");
      }
      if (clicked === "not_completed") activeFilters.delete("completed");

      activeFilters.add(clicked);
    }

    if (activeFilters.size === 0) {
      activeFilters.add("all");
    }
  }

  updateFilterUI();
  currentPage = 0;
  loadFiles();
}

function updateFilterUI() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    const val = btn.getAttribute("data-filter");
    if (activeFilters.has(val)) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });

  const summaryBar = document.getElementById("filterSummaryBar");
  const tagsContainer = document.getElementById("activeFilterTags");

  if (!activeFilters.has("all") && activeFilters.size > 0) {
    summaryBar.style.display = "flex";
    const tagsHtml = Array.from(activeFilters).map(f => {
      const label = filterLabels[f] || f;
      return `<span class="filter-pill-tag">${escapeHtml(label)}</span>`;
    }).join(" + ");
    tagsContainer.innerHTML = tagsHtml;
  } else {
    summaryBar.style.display = "none";
  }
}

async function loadFiles() {
  try {
    const offset = currentPage * pageSize;
    const filterParam = Array.from(activeFilters).join(",");
    const res = await fetch(`/api/files?filter=${encodeURIComponent(filterParam)}&search=${encodeURIComponent(currentSearch)}&limit=${pageSize}&offset=${offset}`);
    const data = await res.json();
    renderFilesTable(data.files, data.total);
  } catch (e) {
    console.error("Failed to load files", e);
  }
}

function renderFilesTable(files, total) {
  const tbody = document.getElementById("filesTableBody");
  tbody.innerHTML = "";

  if (!files || files.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4">Tidak ada file yang sesuai kriteria.</td></tr>`;
    document.getElementById("pageInfo").textContent = `Halaman 0 dari 0`;
    document.getElementById("btnPrevPage").disabled = true;
    document.getElementById("btnNextPage").disabled = true;
    return;
  }

  const totalPages = Math.ceil(total / pageSize);
  document.getElementById("pageInfo").textContent = `Halaman ${currentPage + 1} dari ${totalPages} (${total.toLocaleString()} file)`;
  document.getElementById("btnPrevPage").disabled = currentPage <= 0;
  document.getElementById("btnNextPage").disabled = currentPage >= totalPages - 1;

  files.forEach(f => {
    const tr = document.createElement("tr");

    // File info & Title (metadata title first if exists, otherwise filename; always embed filename underneath)
    const displayTitle = (f.title && f.title.trim()) ? f.title.trim() : f.filename;
    const titleCell = `
      <td>
        <div class="media-title">${escapeHtml(displayTitle)}</div>
        <div class="media-filename-sub" title="Nama File: ${escapeHtml(f.filename)}">
          <span class="file-icon">📁</span> ${escapeHtml(f.filename)}
        </div>
        <div class="path-sub">${escapeHtml(f.relative_path)}</div>
      </td>
    `;

    // Resolution
    const resCell = `<td>${f.width}x${f.height}</td>`;

    // Video stream badges
    let vBadges = `<span class="badge badge-codec">${f.video_codec.toUpperCase()}</span>`;
    if (f.bit_depth > 8) vBadges += `<span class="badge badge-10bit">${f.bit_depth}-bit</span>`;
    if (f.is_hdr === 1) vBadges += `<span class="badge badge-hdr">HDR</span>`;
    const vCell = `<td>${vBadges}</td>`;

    // Audio stream summary (with commentary distinction)
    let audioHtml = "";
    if (f.audio_summary) {
      const parts = f.audio_summary.split(", ");
      const badges = parts.map(p => {
        const isComm = p.includes("[COMM]");
        const cls = isComm ? "badge-audio-comm" : "badge-audio";
        const titleAttr = isComm ? ' title="Audio Komentar / Tambahan (Bukan Audio Utama)"' : "";
        return `<span class="badge ${cls}"${titleAttr}>${escapeHtml(p)}</span>`;
      });
      audioHtml = `<div class="audio-badges-wrap">${badges.join("")}</div>`;
    } else {
      audioHtml = `<span class="badge badge-audio">No audio</span>`;
    }
    const audioCell = `<td>${audioHtml}</td>`;

    // Subtitles stream & external file summary
    let subHtml = "";
    if (f.has_subtitles === 1) {
      const subItems = [];
      try {
        const intSubs = JSON.parse(f.internal_subs || "[]");
        intSubs.forEach(s => {
          const lang = (s.language || 'und').toUpperCase();
          const codec = (s.codec || '').toUpperCase();
          const codecLabel = codec.includes("SUBRIP") ? "SRT" : (codec.includes("PGS") ? "PGS" : ((codec.includes("ASS") || codec.includes("SSA")) ? "ASS" : codec));
          const flags = [];
          if (s.default) flags.push("def");
          if (s.forced) flags.push("forced");
          const flagStr = flags.length ? ` (${flags.join(',')})` : "";
          const isIndo = ["IND", "ID"].includes(lang);
          const badgeClass = isIndo ? "badge-sub-indo" : (s.codec.includes("pgs") ? "badge-sub-pgs" : "badge-sub-int");
          subItems.push(`<span class="badge ${badgeClass}" title="Internal: ${escapeHtml(s.title || codec)}">INT: ${lang}:${codecLabel}${flagStr}</span>`);
        });
      } catch (e) {}

      try {
        const extSubs = JSON.parse(f.external_subs || "[]");
        extSubs.forEach(s => {
          const lang = (s.language || 'UND').toUpperCase();
          const isIndo = ["IND", "ID"].includes(lang);
          const badgeClass = isIndo ? "badge-sub-indo" : "badge-sub-ext";
          subItems.push(`<span class="badge ${badgeClass}" title="Eksternal: ${escapeHtml(s.path)}">EXT: ${escapeHtml(s.filename)} [${lang}]</span>`);
        });
      } catch (e) {}

      subHtml = subItems.length ? `<div class="sub-badges-wrap">${subItems.join("")}</div>` : `<span class="badge badge-sub-none">Tanpa Subtitle</span>`;
    } else {
      subHtml = `<span class="badge badge-sub-none">Tanpa Subtitle</span>`;
    }
    const subCell = `<td>${subHtml}</td>`;

    // Transcode reasons / status
    let reasonsHtml = "";
    if (f.needs_transcode === 1) {
      try {
        const reasons = JSON.parse(f.transcode_reasons || "[]");
        reasonsHtml = reasons.map(r => `<span class="badge badge-reason">${escapeHtml(r)}</span>`).join("<br>");
      } catch (e) {
        reasonsHtml = `<span class="badge badge-reason">Wajib Konversi</span>`;
      }
    } else {
      reasonsHtml = `<span class="badge badge-ok">Kompatibel Penuh</span>`;
    }
    const statusCell = `<td>${reasonsHtml}</td>`;

    // Action button
    let actionBtn = "";
    if (f.status === "completed" || f.queue_status === "completed") {
      actionBtn = `
        <div class="action-btn-group">
          <span class="badge badge-ok">✔ Selesai</span>
          <button class="btn btn-outline btn-sm" onclick="resetFileStatus(${f.id})" title="Batal tandai selesai">↺ Reset</button>
        </div>
      `;
    } else if (f.queue_status === "running") {
      actionBtn = `<span class="text-info font-bold">Sedang Diproses...</span>`;
    } else if (f.queue_status === "pending") {
      actionBtn = `
        <div class="action-btn-group">
          <span class="text-warn">Dalam Antrean</span>
          <button class="btn btn-outline btn-sm" onclick="markAsCompleted(${f.id})" title="Tandai sudah pernah dikonversi / sudah ada di Jellyfin">✔ Selesai</button>
        </div>
      `;
    } else {
      actionBtn = `
        <div class="action-btn-group">
          <button class="btn btn-primary btn-sm" onclick="addToQueue(${f.id})">+ Antrean</button>
          <button class="btn btn-outline btn-sm" onclick="markAsCompleted(${f.id})" title="Tandai sudah pernah dikonversi / sudah ada di Jellyfin">✔ Selesai</button>
        </div>
      `;
    }
    const actionCell = `<td>${actionBtn}</td>`;

    tr.innerHTML = actionCell + titleCell + resCell + vCell + audioCell + subCell + statusCell;
    tbody.appendChild(tr);
  });
}

async function loadQueue() {
  try {
    const res = await fetch("/api/queue");
    const data = await res.json();
    const queue = data.queue || [];

    const running = queue.filter(q => q.status === "running");
    const pending = queue.filter(q => q.status !== "running");

    renderActiveWorkers(running);
    renderQueueTable(pending);
  } catch (e) {
    console.error("Failed to load queue", e);
  }
}

function renderActiveWorkers(runningTasks) {
  const container = document.getElementById("activeWorkersContainer");
  container.innerHTML = "";

  if (runningTasks.length === 0) {
    container.style.display = "none";
    return;
  }
  container.style.display = "grid";

  runningTasks.forEach((t, i) => {
    const card = document.createElement("div");
    card.className = "worker-card";

    const etaText = t.eta_seconds > 0 ? formatEta(t.eta_seconds) : "Menghitung...";
    const speedText = t.current_speed ? t.current_speed : "--";
    const fpsText = t.current_fps ? `${t.current_fps} fps` : "--";

    const displayTitle = (t.title && t.title.trim()) ? t.title.trim() : t.filename;

    card.innerHTML = `
      <div class="worker-card-header">
        <div class="worker-title" title="${escapeHtml(t.filename)}">
          ⚡ Worker #${t.worker_id + 1}: <strong>${escapeHtml(displayTitle)}</strong>
          <div class="media-filename-sub" style="margin-top: 2px;"><span class="file-icon">📁</span> ${escapeHtml(t.filename)}</div>
        </div>
        <button class="btn btn-danger btn-sm" onclick="cancelTask(${t.id})">Batalkan</button>
      </div>
      <div class="progress-bar">
        <div class="progress-fill" style="width: ${t.progress_percent}%;"></div>
      </div>
      <div class="worker-stats">
        <span>${t.progress_percent}%</span>
        <span>Speed: ${speedText}</span>
        <span>FPS: ${fpsText}</span>
        <span>ETA: ${etaText}</span>
      </div>
    `;
    container.appendChild(card);
  });
}

function renderQueueTable(items) {
  const table = document.getElementById("queueTable");
  const tbody = document.getElementById("queueTableBody");
  const empty = document.getElementById("queueEmptyNotice");

  if (!items || items.length === 0) {
    table.style.display = "none";
    empty.style.display = "block";
    return;
  }

  table.style.display = "table";
  empty.style.display = "none";
  tbody.innerHTML = "";

  items.forEach(item => {
    const tr = document.createElement("tr");

    let statusBadge = "";
    if (item.status === "pending") statusBadge = `<span class="badge" style="background:#475569;">Menunggu</span>`;
    else if (item.status === "completed") statusBadge = `<span class="badge badge-ok">Selesai</span>`;
    else if (item.status === "failed") statusBadge = `<span class="badge" style="background:#ef4444;">Gagal</span>`;
    else if (item.status === "cancelled") statusBadge = `<span class="badge" style="background:#64748b;">Dibatalkan</span>`;

    const displayTitle = (item.title && item.title.trim()) ? item.title.trim() : item.filename;

    tr.innerHTML = `
      <td>
        <button class="btn btn-outline btn-sm" onclick="removeFromQueue(${item.id})">Hapus</button>
      </td>
      <td>${statusBadge}</td>
      <td>
        <div class="media-title">${escapeHtml(displayTitle)}</div>
        <div class="media-filename-sub"><span class="file-icon">📁</span> ${escapeHtml(item.filename)}</div>
      </td>
      <td>${item.progress_percent}%</td>
      <td>${item.current_speed || '--'}</td>
      <td>${item.eta_seconds > 0 ? formatEta(item.eta_seconds) : '--'}</td>
    `;
    tbody.appendChild(tr);
  });
}

// Action Handlers
async function startScan() {
  await fetch("/api/scan/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  pollStats();
}

async function startSubtitleScan() {
  await fetch("/api/scan/subtitles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force: false })
  });
  pollStats();
}

async function stopScan() {
  await fetch("/api/scan/stop", { method: "POST" });
  pollStats();
}

async function startQueue() {
  await fetch("/api/queue/start", { method: "POST" });
  pollStats();
  loadQueue();
}

async function pauseQueue() {
  await fetch("/api/queue/pause", { method: "POST" });
  pollStats();
  loadQueue();
}

async function addToQueue(mediaId) {
  await fetch("/api/queue/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_id: mediaId })
  });
  loadFiles();
  loadQueue();
}

async function addAllToQueue() {
  if (confirm("Tambahkan seluruh film/series yang wajib dikonversi ke dalam antrean?")) {
    const res = await fetch("/api/queue/add_all", { method: "POST" });
    const data = await res.json();
    alert(`Berhasil menambahkan ${data.added_count} video ke dalam antrean.`);
    loadFiles();
    loadQueue();
  }
}

async function markAsCompleted(mediaId) {
  await fetch("/api/files/mark_completed", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_id: mediaId })
  });
  loadFiles();
  loadQueue();
  pollStats();
}

async function resetFileStatus(mediaId) {
  await fetch("/api/files/reset_status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_id: mediaId })
  });
  loadFiles();
  loadQueue();
  pollStats();
}

async function removeFromQueue(queueId) {
  await fetch("/api/queue/remove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ queue_id: queueId })
  });
  loadQueue();
  loadFiles();
}

async function cancelTask(queueId) {
  if (confirm("Batalkan proses render file ini?")) {
    await fetch("/api/queue/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ queue_id: queueId })
    });
    loadQueue();
    loadFiles();
  }
}

async function clearCompletedQueue() {
  await fetch("/api/queue/clear_completed", { method: "POST" });
  loadQueue();
  loadFiles();
  pollStats();
}

function updateSourceDirLabels(srcDir) {
  const sub = document.getElementById("statTotalSub");
  const btn = document.getElementById("btnStartScan");
  if (sub && srcDir) sub.textContent = srcDir;
  if (btn && srcDir) btn.textContent = `🔍 Mulai Scan (${srcDir})`;
}

async function saveSettings() {
  const source_dir = document.getElementById("settingSourceDir").value.trim();
  const output_dir = document.getElementById("settingOutputDir").value.trim();
  const staging_dir = document.getElementById("settingStagingDir").value.trim();
  const encoder = document.getElementById("settingEncoder").value;
  const concurrent_workers = document.getElementById("settingWorkers").value;

  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_dir, output_dir, staging_dir, encoder, concurrent_workers })
  });

  updateSourceDirLabels(source_dir);
  alert("Pengaturan berhasil disimpan.");
  loadSystemInfo();
}

function formatEta(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const remM = m % 60;
    return `${h}j ${remM}m`;
  }
  return `${m}m ${s}d`;
}

function escapeHtml(text) {
  if (!text) return "";
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
