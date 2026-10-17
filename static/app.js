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
  ]
};

const $ = (id) => document.getElementById(id);

/* ============ 初始化 ============ */
document.addEventListener("DOMContentLoaded", () => {
  initNav();
  initSelects();
  initChips();
  initUpload("i2i", 1);
  initUpload("multi", 6);
  initGenerate("t2i", "text2img");
  initGenerate("i2i", "img2img");
  initGenerate("multi", "multi");
  checkKey();
});

/* ---- 侧边栏导航 ---- */
function initNav() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => {
      if (item.classList.contains("disabled")) {
        toast("视频生成排队拥堵中，接口暂时不可用，恢复后开放", "err");
        return;
      }
      document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
      document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
      item.classList.add("active");
      $("page-" + item.dataset.page).classList.add("active");
    });
  });
}

/* ---- 尺寸/比例下拉 + 实时输出尺寸 ---- */
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

/* ---- 图片上传（点击 + 拖拽），存 Data URI ---- */
const uploaded = { i2i: [], multi: [] };

function initUpload(key, max) {
  const drop = $(key + "-drop");
  const input = $(key + "-file");

  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => addFiles(key, input.files, max));

  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("dragover"); })
  );
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("dragover"); })
  );
  drop.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files.length) addFiles(key, e.dataTransfer.files, max);
  });
}

function addFiles(key, fileList, max) {
  const files = Array.from(fileList).filter((f) => f.type.startsWith("image/"));
  if (!files.length) { toast("请选择图片文件", "err"); return; }
  for (const f of files) {
    if (uploaded[key].length >= max) { toast(`最多 ${max} 张图片`, "err"); break; }
    const reader = new FileReader();
    reader.onload = () => {
      uploaded[key].push({ name: f.name, dataUri: reader.result });
      renderThumbs(key, max);
    };
    reader.readAsDataURL(f);
  }
}

function renderThumbs(key, max) {
  const box = $(key + "-thumbs");
  box.innerHTML = "";
  uploaded[key].forEach((item, idx) => {
    const div = document.createElement("div");
    div.className = "thumb";
    div.innerHTML =
      `<img src="${item.dataUri}" alt="">` +
      `<span class="thumb-order">第 ${idx + 1} 张</span>` +
      `<button class="thumb-del" title="移除">×</button>`;
    div.querySelector(".thumb-del").addEventListener("click", () => {
      uploaded[key].splice(idx, 1);
      renderThumbs(key, max);
    });
    box.appendChild(div);
  });
}

/* ---- 生成 ---- */
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
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}
