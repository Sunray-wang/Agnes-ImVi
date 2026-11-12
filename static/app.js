/* ============ Agnes 生成工作台 — 前端逻辑 ============ */
"use strict";

/* ---- size 档位 × ratio -> 输出像素（与文档一致） ---- */
const DIMS = {
  "1K": { "1:1": "1024x1024", "3:4": "864x1152", "4:3": "1152x864", "16:9": "1312x736",
          "9:16": "736x1312", "2:3": "832x1248", "3:2": "1248x832", "21:9": "1568x672" },
  "2K": { "1:1": "2048x2048", "3:4": "1728x2304", "4:3": "2304x1728", "16:9": "2624x1472",
          "9:16": "1472x2624", "2:3": "1664x2496", "3:2": "2496x1664", "21:9": "3136x1344" },
  "3K": { "1:1": "3072x3072", "3:4": "2592x3456", "4:3": "3456x2592", "16:9": "3936x2208",
          "9:16": "2208x3936", "2:3": "2496x3744", "3:2": "3744x2496", "21:9": "4704x2016" },
  "4K": { "1:1": "4096x4096", "3:4": "3456x4608", "4:3": "4608x3456", "16:9": "5248x2944",
          "9:16": "2944x5248", "2:3": "3328x4992", "3:2": "4992x3328", "21:9": "6272x2688" }
};

/* ---- 视频固定 720P，aspect_ratio -> 输出像素 ---- */
const VIDEO_DIMS = {
  "21:9": "1680x720", "16:9": "1280x720", "4:3": "960x720",
  "1:1": "720x720", "3:4": "720x960", "9:16": "720x1280"
};

/* ---- 各功能的提示词模板片段 ---- */
const TEMPLATES = {
  t2i: [
    ["电影感", "电影级写实风格，戏剧性光影，广角构图，高视觉密度"],
    ["产品图", "干净的产品摄影，纯白背景，柔和阴影，高细节，商业布光"],
    ["插画", "扁平插画风格，明快配色，简洁构图，适合社交媒体封面"]
  ],
  i2i: [
    ["赛博朋克夜景", "改为电影级赛博朋克夜景，添加霓虹招牌和湿滑路面倒影，保留原始构图与主体布局"],
    ["换色", "将主体改为橙色，保留原始构图、材质和光照"],
    ["极简线稿", "转为极简线稿风格，仅保留主体轮廓，去除背景细节，保留原始构图"]
  ],
  multi: [
    ["角色+产品海报", "第一张图作为主要角色，第二张图作为产品参考，生成电影级活动海报，保留角色身份和产品外形，自然光照，干净的商业构图"],
    ["人物融合场景", "将两张图中的人物自然地融合进同一场景，统一光照方向与色温，电影级构图"]
  ],
  t2v: [
    ["窗台小猫", "一只橘色小猫慢慢走过洒满阳光的窗台，毛发被微风轻轻吹动，电影感浅景深，镜头缓慢横移"],
    ["城市延时", "城市天际线从日出到正午的光影流转，云层快速流动，车流如织，电影级延时摄影质感"]
  ],
  kf: [
    ["起身走近", "小猫从窗台站起身，缓缓走向镜头，最后看向观众，动作自然连贯，光照保持一致"],
    ["昼夜转换", "场景从首帧的白天平滑过渡到尾帧的夜晚，灯光渐次亮起，氛围宁静"]
  ],
  ref: [
    ["小猫散步", "第 1 张图的小猫从窗台跳下，伸个懒腰后慢慢走远，尾巴轻轻摇摆，背景阳光摇曳，电影感"],
    ["双图互动", "第 1 张图的小猫走向第 2 张图的小猫，互相蹭头打招呼，动作自然，光照统一"]
  ],
  audio: [
    ["节奏摇摆", "第 1 张图的小猫随着音频 1 的节奏轻轻摇摆脑袋，画面温馨可爱，光照自然"],
    ["音乐会", "第 1 张图的场景随音频 1 的旋律起伏变化，光影律动，电影感"]
  ]
};

const $ = (id) => document.getElementById(id);

/* ---- HTML 转义（文件名等不可信文本） ---- */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---- 各页面上传的素材（Data URI） ---- */
const uploaded = {
  i2i: [], multi: [],           // 图像页
  kfFirst: [], kfLast: [],      // 首尾帧
  ref: [],                      // 图生视频参考图
  audioImg: [], audioSnd: []    // 音频参考：图 + 音频
};

/* ============ 初始化 ============ */
document.addEventListener("DOMContentLoaded", () => {
  initNav();
  initSelects();
  initChips();
  initUpload("i2i", 1, "image/*", "image");
  initUpload("multi", 6, "image/*", "image");
  initUpload("kfFirst", 1, "image/*", "image");
  initUpload("kfLast", 1, "image/*", "image");
  initUpload("ref", 5, "image/*", "image");
  initUpload("audioImg", 5, "image/*", "image");
  initUpload("audioSnd", 3, "audio/*", "audio");
  initVideoSelects();
  initGenerate("t2i", "text2img");
  initGenerate("i2i", "img2img");
  initGenerate("multi", "multi");
  initVideoGenerate("t2v", "text2video");
  initVideoGenerate("kf", "keyframe");
  initVideoGenerate("ref", "reference");
  initVideoGenerate("audio", "reference");
  checkKey();
});

/* ---- 侧边栏导航 ---- */
function initNav() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
      document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
      item.classList.add("active");
      $("page-" + item.dataset.page).classList.add("active");
    });
  });
}

/* ---- 尺寸/比例下拉 + 实时输出尺寸（图像） ---- */
function initSelects() {
  ["t2i", "i2i", "multi"].forEach((key) => {
    const sizeSel = $(key + "-size");
    const ratioSel = $(key + "-ratio");
    Object.keys(DIMS).forEach((s) => sizeSel.add(new Option(s, s)));
    Object.keys(DIMS["1K"]).forEach((r) => ratioSel.add(new Option(r, r)));
    ratioSel.value = "1:1";
    const update = () => {
      $(key + "-dims").textContent = DIMS[sizeSel.value][ratioSel.value] || "—";
    };
    sizeSel.addEventListener("change", update);
    ratioSel.addEventListener("change", update);
    update();
  });
}

/* ---- 视频参数：时长 + 宽高比 + 实时输出尺寸 ---- */
function initVideoSelects() {
  ["t2v", "kf", "ref", "audio"].forEach((key) => {
    const secSel = $(key + "-seconds");
    const ratioSel = $(key + "-ratio");
    for (let s = 4; s <= 12; s++) secSel.add(new Option(s + " 秒", String(s)));
    secSel.value = "4";
    Object.keys(VIDEO_DIMS).forEach((r) => ratioSel.add(new Option(r, r)));
    ratioSel.value = "16:9";
    const update = () => {
      $(key + "-dims").textContent = VIDEO_DIMS[ratioSel.value] || "—";
    };
    ratioSel.addEventListener("change", update);
    update();
  });
}

/* ---- 提示词模板 chips ---- */
function initChips() {
  Object.entries(TEMPLATES).forEach(([key, list]) => {
    const box = $(key + "-chips");
    list.forEach(([label, text]) => {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.textContent = label + " 模板";
      chip.addEventListener("click", () => {
        $(key + "-prompt").value = text;
        $(key + "-prompt").focus();
      });
      box.appendChild(chip);
    });
  });
}

/* ---- 文件上传（点击 + 拖拽），存 Data URI ---- */
function initUpload(key, max, accept, kind) {
  const drop = $(key + "-drop");
  const input = $(key + "-file");
  if (!drop || !input) return;

  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => { addFiles(key, input.files, max, kind); input.value = ""; });

  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("dragover"); })
  );
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("dragover"); })
  );
  drop.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files.length) addFiles(key, e.dataTransfer.files, max, kind);
  });
}

function addFiles(key, fileList, max, kind) {
  const prefix = kind === "audio" ? "audio/" : "image/";
  const files = Array.from(fileList).filter((f) => f.type.startsWith(prefix));
  if (!files.length) { toast(kind === "audio" ? "请选择音频文件" : "请选择图片文件", "err"); return; }
  for (const f of files) {
    if (uploaded[key].length >= max) { toast(`最多 ${max} 个文件`, "err"); break; }
    const reader = new FileReader();
    reader.onload = () => {
      uploaded[key].push({ name: f.name, dataUri: reader.result, kind });
      renderThumbs(key, max);
    };
    reader.readAsDataURL(f);
  }
}

function renderThumbs(key, max) {
  const box = $(key + "-thumbs");
  if (!box) return;
  box.innerHTML = "";
  uploaded[key].forEach((item, idx) => {
    const div = document.createElement("div");
    div.className = "thumb" + (item.kind === "audio" ? " thumb-audio" : "");
    const label = item.kind === "audio" ? "音频 " + (idx + 1) : "第 " + (idx + 1) + " 张";
    div.innerHTML =
      (item.kind === "audio"
        ? `<div class="audio-icon">♪</div>`
        : `<img src="${item.dataUri}" alt="">`) +
      `<span class="thumb-order">${label}</span>` +
      `<span class="thumb-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>` +
      `<button class="thumb-del" title="移除">×</button>`;
    div.querySelector(".thumb-del").addEventListener("click", () => {
      uploaded[key].splice(idx, 1);
      renderThumbs(key, max);
    });
    box.appendChild(div);
  });
}

/* ============ 图像生成 ============ */

function initGenerate(key, endpoint) {
  $(key + "-go").addEventListener("click", async () => {
    const prompt = $(key + "-prompt").value.trim();
    if (!prompt) { toast("请先输入提示词", "err"); return; }

    const body = {
      prompt,
      size: $(key + "-size").value,
      ratio: $(key + "-ratio").value
    };
    if (endpoint === "img2img") {
      if (!uploaded.i2i.length) { toast("请先上传一张输入图片", "err"); return; }
      body.image = uploaded.i2i[0].dataUri;
    }
    if (endpoint === "multi") {
      if (uploaded.multi.length < 2) { toast("多图合成至少需要 2 张参考图", "err"); return; }
      body.images = uploaded.multi.map((x) => x.dataUri);
    }

    const btn = $(key + "-go");
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>生成中（繁忙时自动重试）…`;

    const skeleton = showSkeleton(key);

    try {
      const resp = await fetch(`/api/image/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      skeleton.remove();
      addResultCard(key, data, body);
    } catch (err) {
      skeleton.remove();
      toast("生成失败：" + err.message, "err");
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  });
}

function showSkeleton(key) {
  const box = $(key + "-results");
  const div = document.createElement("div");
  div.className = "skeleton";
  div.innerHTML = `<div class="sk-img"></div><div class="sk-line"></div>`;
  box.prepend(div);
  return div;
}

function addResultCard(key, data, body) {
  const box = $(key + "-results");
  const card = document.createElement("div");
  card.className = "result-card";
  const kb = data.bytes ? (data.bytes / 1024).toFixed(0) + " KB" : "";
  card.innerHTML =
    `<div class="img-wrap"><img src="${data.src}" alt="生成结果"></div>` +
    `<div class="result-meta">` +
      `<span class="dims-line">${data.expected || "—"} · ${kb}</span>` +
      `<span>耗时 ${data.elapsed}s · ${body.size} · ${body.ratio}</span>` +
    `</div>` +
    `<div class="result-actions">` +
      `<button class="btn ghost act-dl">下载图片</button>` +
      `<button class="btn ghost act-copy">复制参数</button>` +
    `</div>`;
  card.querySelector(".act-dl").addEventListener("click", () => {
    window.location.href = data.src + "?download=1";
  });
  card.querySelector(".act-copy").addEventListener("click", () => {
    const text = `prompt: ${body.prompt}\nsize: ${body.size}\nratio: ${body.ratio}`;
    navigator.clipboard.writeText(text).then(
      () => toast("参数已复制"),
      () => toast("复制失败，请手动选择", "err")
    );
  });
  box.prepend(card);
}

/* ============ 视频生成 ============ */

function videoBody(key) {
  const body = {
    prompt: $(key + "-prompt").value.trim(),
    seconds: $(key + "-seconds").value,
    aspect_ratio: $(key + "-ratio").value
  };
  if (key === "kf") {
    if (!uploaded.kfFirst.length) { toast("请先上传首帧图片", "err"); return null; }
    body.first_frame = uploaded.kfFirst[0].dataUri;
    if (uploaded.kfLast.length) body.last_frame = uploaded.kfLast[0].dataUri;
  }
  if (key === "ref") {
    if (!uploaded.ref.length) { toast("请先上传至少 1 张参考图", "err"); return null; }
    body.images = uploaded.ref.map((x) => x.dataUri);
  }
  if (key === "audio") {
    if (!uploaded.audioImg.length) { toast("请先上传至少 1 张参考图", "err"); return null; }
    if (!uploaded.audioSnd.length) { toast("请先上传至少 1 段音频", "err"); return null; }
    body.images = uploaded.audioImg.map((x) => x.dataUri);
    body.audios = uploaded.audioSnd.map((x) => x.dataUri);
  }
  return body;
}

function initVideoGenerate(key, endpoint) {
  $(key + "-go").addEventListener("click", async () => {
    const prompt = $(key + "-prompt").value.trim();
    if (!prompt) { toast("请先输入提示词", "err"); return; }
    const body = videoBody(key);
    if (!body) return;

    const btn = $(key + "-go");
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span>提交中…`;

    const card = showVideoProgress(key);
    let pollTimer = null;
    let netErrors = 0;   // 连续网络错误计数（服务重启时容忍）

    const stopPolling = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } };
    const resetBtn = () => { btn.disabled = false; btn.innerHTML = original; };

    try {
      const resp = await fetch(`/api/video/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);

      card.setStatus("任务已提交，排队创建中…", 0);
      const jobId = data.job_id;
      resetBtn();  // 创建已秒回，恢复按钮，允许同时发起多个任务

      pollTimer = setInterval(async () => {
        try {
          const r = await fetch(`/api/video/status/${encodeURIComponent(jobId)}`);
          const d = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);

          if (d.status === "completed") {
            stopPolling();
            if (d.src) {
              card.finish(d, body);
            } else {
              card.fail("视频已生成，但自动下载失败（结果 URL 时效极短）。" +
                        (d.error ? "原因：" + d.error : "") +
                        (d.source_url ? " 可尝试打开原始链接。" : ""));
            }
            resetBtn();
          } else if (d.status === "completed_download_failed") {
            stopPolling();
            card.fail("视频已生成，但自动下载失败（结果 URL 时效极短）。原因：" +
                      (d.error || "未知") +
                      (d.source_url ? " 原始链接：" + d.source_url : ""));
            resetBtn();
          } else if (d.status === "failed") {
            stopPolling();
            card.fail("任务失败：" + (d.error || "未知原因"));
            resetBtn();
          } else if (d.status === "unknown") {
            stopPolling();
            card.fail("任务记录丢失（服务可能重启过），请重新生成");
            resetBtn();
          } else if (d.status === "creating") {
            const n = Number(d.create_attempt) || 0;
            card.setStatus(n > 0 ? `排队创建中（拥堵重试第 ${n} 次）…` : "排队创建中…", 0);
          } else {
            const p = Number(d.progress) || 0;
            card.setStatus(`生成中 ${p}%`, p);
          }
        } catch (err) {
          // TypeError（Failed to fetch）= 网络层错误：服务可能正在重启，
          // 容忍连续 5 次，期间继续轮询；超限才判失败
          if (err instanceof TypeError && netErrors < 5) {
            netErrors++;
            card.setStatus(`服务连接中断，重试中（${netErrors}/5）…`, 0);
            return;
          }
          stopPolling();
          card.fail("轮询失败：" + err.message);
          resetBtn();
        }
      }, 3000);
    } catch (err) {
      card.remove();
      toast("创建失败：" + err.message, "err");
      resetBtn();
    }
  });
}

function showVideoProgress(key) {
  const box = $(key + "-results");
  const card = document.createElement("div");
  card.className = "result-card video-progress";
  card.innerHTML =
    `<div class="video-progress-box">` +
      `<div class="vp-spinner"></div>` +
      `<div class="vp-status">创建任务中…</div>` +
      `<div class="vp-bar"><div class="vp-bar-fill"></div></div>` +
      `<div class="vp-hint">任务提交后自动排队，拥堵时后端自动重试；视频生成需要数分钟，可以离开此页</div>` +
    `</div>`;
  box.prepend(card);

  const statusEl = card.querySelector(".vp-status");
  const fillEl = card.querySelector(".vp-bar-fill");

  return {
    setStatus(text, pct) {
      statusEl.textContent = text;
      fillEl.style.width = Math.max(0, Math.min(100, pct)) + "%";
    },
    finish(data, body) {
      card.classList.remove("video-progress");
      card.classList.add("video-done");
      const kb = data.bytes ? (data.bytes / 1024 / 1024).toFixed(1) + " MB" : "";
      card.innerHTML =
        `<div class="video-wrap"><video controls preload="metadata" src="${data.src}"></video></div>` +
        `<div class="result-meta">` +
          `<span class="dims-line">${body.aspect_ratio} · ${kb}</span>` +
          `<span>720P · ${body.seconds} 秒</span>` +
        `</div>` +
        `<div class="result-actions">` +
          `<button class="btn ghost act-dl">下载视频</button>` +
          `<button class="btn ghost act-copy">复制参数</button>` +
        `</div>`;
      card.querySelector(".act-dl").addEventListener("click", () => {
        window.location.href = data.src + "?download=1";
      });
      card.querySelector(".act-copy").addEventListener("click", () => {
        const text = `prompt: ${body.prompt}\nseconds: ${body.seconds}\naspect_ratio: ${body.aspect_ratio}`;
        navigator.clipboard.writeText(text).then(
          () => toast("参数已复制"),
          () => toast("复制失败，请手动选择", "err")
        );
      });
    },
    fail(msg) {
      card.remove();
      toast(msg, "err");
    },
    remove() { card.remove(); }
  };
}

/* ---- Key 状态 ---- */
async function checkKey() {
  try {
    const r = await fetch("/api/health");
    const d = await r.json();
    $("keyDot").className = "dot " + (d.key_configured ? "ok" : "err");
    $("keyText").textContent = d.key_configured ? "API Key 已配置" : "API Key 未配置";
  } catch {
    $("keyDot").className = "dot err";
    $("keyText").textContent = "服务连接失败";
  }
}

/* ---- Toast ---- */
let toastTimer = null;
function toast(msg, type) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (type === "err" ? " err" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 5000);
}
