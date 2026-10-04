/* FlyAI 前端 · 图片附件 / Pixiv 搜图候选墙 / 全屏看图器（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

/* ==== 图片附件（视觉模型）==== */
var IMAGE_MAX_COUNT = 4;
var IMAGE_MAX_EDGE = 1568;   // 压缩后最长边
var IMAGE_QUALITY = 0.82;
var IMAGE_MAX_FILE = 8 * 1024 * 1024;

function imageDataUrl(img) {
  return "data:" + img.media_type + ";base64," + img.data;
}
function addImageFiles(fileList) {
  if (!visionEnabled()) {
    showToast("当前模型不支持图片输入，请在设置中为视觉模型勾选「视觉」");
    return;
  }
  var files = Array.prototype.filter.call(fileList || [], function (f) {
    return f.type.indexOf("image/") === 0;
  });
  if (!files.length) return;
  // 串行处理，处理完一张就刷新缩略图
  files.reduce(function (p, f) {
    return p.then(function () {
      if (state.pendingImages.length >= IMAGE_MAX_COUNT) {
        showToast("每条消息最多附带 " + IMAGE_MAX_COUNT + " 张图片");
        return;
      }
      return processImageFile(f).then(function (img) {
        state.pendingImages.push(img);
        renderPendingImages();
      }).catch(function (e) {
        showToast("图片处理失败（" + (f.name || "") + "）：" + e.message, "err");
      });
    });
  }, Promise.resolve());
}
function processImageFile(file) {
  if (file.size > IMAGE_MAX_FILE) return Promise.reject(new Error("文件超过 8MB"));
  return new Promise(function (resolve, reject) {
    var reader = new FileReader();
    reader.onerror = function () { reject(new Error("读取失败")); };
    reader.onload = function () {
      var pic = new Image();
      pic.onerror = function () { reject(new Error("无法解析为图片")); };
      pic.onload = function () {
        var scale = Math.min(1, IMAGE_MAX_EDGE / Math.max(pic.width, pic.height));
        var cw = Math.max(1, Math.round(pic.width * scale));
        var ch = Math.max(1, Math.round(pic.height * scale));
        var canvas = document.createElement("canvas");
        canvas.width = cw;
        canvas.height = ch;
        var ctx2d = canvas.getContext("2d");
        ctx2d.fillStyle = "#ffffff";  // 透明 PNG 转 JPEG 先铺白底
        ctx2d.fillRect(0, 0, cw, ch);
        ctx2d.drawImage(pic, 0, 0, cw, ch);
        canvas.toBlob(function (blob) {
          if (!blob) { reject(new Error("压缩失败")); return; }
          var fr = new FileReader();
          fr.onload = function () {
            var comma = fr.result.indexOf(",");
            resolve({ media_type: "image/jpeg", data: fr.result.slice(comma + 1) });
          };
          fr.onerror = function () { reject(new Error("读取失败")); };
          fr.readAsDataURL(blob);
        }, "image/jpeg", IMAGE_QUALITY);
      };
      pic.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}
function renderPendingImages() {
  el.attachPreview.innerHTML = "";
  el.attachPreview.classList.toggle("hidden", !state.pendingImages.length);
  state.pendingImages.forEach(function (img, i) {
    var cell = document.createElement("div");
    cell.className = "attach-cell";
    var pic = document.createElement("img");
    pic.src = imageDataUrl(img);
    cell.appendChild(pic);
    var x = document.createElement("button");
    x.type = "button";
    x.className = "attach-x";
    x.title = "移除";
    x.textContent = "×";
    x.addEventListener("click", function () {
      state.pendingImages.splice(i, 1);
      renderPendingImages();
    });
    cell.appendChild(x);
    el.attachPreview.appendChild(cell);
  });
}

function extractGeneratedImage(text) {
  var m = /\[(?:图片已生成|图片)\]\s*(\S+)/.exec(text || "");
  return m ? m[1] : "";
}
// 从工具输出中提取 [图片参数] 单行 JSON（看图器按生图方式展示）
function extractImageMeta(text) {
  var m = /^\[图片参数\]\s*(\{.*\})\s*$/m.exec(text || "");
  if (!m) return null;
  try { return JSON.parse(m[1]); } catch (e) { return null; }
}
// 图片标记输出：卡片只渲染图片，不显示任何文本
function isImageMarkerText(text) {
  return /^\[(?:图片已生成|图片)\]/.test(text || "");
}
function makeGeneratedImage(url, meta) {
  var img = document.createElement("img");
  img.className = "gen-img";
  img.src = url;
  img.alt = "AI 生成图片";
  img._imgMeta = meta || null;
  return img;
}

/* ==== P 站搜图：候选墙 + 毛玻璃 → 原图揭示 ==== */
function isPixivCandidatesText(text) {
  return /^\[P站候选\]/.test(text || "");
}
function isPixivPicksText(text) {
  return /^\[P站精选\]/.test(text || "");
}
// [候] <编号> <pid> <url>
function parsePixivCands(text) {
  var out = [];
  var re = /\[候\]\s+(\d+)\s+(\d+)\s+(\S+)/g, m;
  while ((m = re.exec(text || ""))) {
    out.push({ index: parseInt(m[1], 10), pid: m[2], url: m[3] });
  }
  return out;
}
// [选] <编号> <pid> <url>
function parsePixivPicks(text) {
  var out = [];
  var re = /\[选\]\s+(\d+)\s+(\d+)\s+(\S+)/g, m;
  while ((m = re.exec(text || ""))) {
    out.push({ index: parseInt(m[1], 10), pid: m[2], url: m[3] });
  }
  return out;
}
// 从全部消息中建立 搜索工具消息id -> {候选编号: 原图URL}
function buildPixivReveal(messages) {
  var map = {};
  var cur = null;
  (messages || []).forEach(function (m) {
    if (m.role !== "tool") return;
    if (m.name === "search_pixiv") {
      cur = m.tool_call_id;
      map[cur] = {};
    } else if (m.name === "pick_pixiv" && cur) {
      parsePixivPicks(m.content).forEach(function (p) {
        map[cur][p.index] = p.url;
      });
    }
  });
  return map;
}
function makePixivGrid(cands, revealed, live) {
  // live=true：未精选项渲染模糊缩略图；false：未精选项为无网络请求的毛玻璃占位
  var grid = document.createElement("div");
  grid.className = "pixiv-grid";
  cands.forEach(function (c) {
    var item = document.createElement("figure");
    item.className = "px-item";
    item.dataset.index = String(c.index);
    var frame = document.createElement("div");
    frame.className = "px-frame";
    var finalUrl = revealed ? revealed[c.index] : null;
    if (finalUrl) {
      frame.appendChild(makePxImg(finalUrl, false, c.pid));
      frame.classList.add("revealed");
      item.classList.add("picked");
    } else if (live) {
      frame.appendChild(makePxImg(c.url, true, c.pid));
    } else {
      var frost = document.createElement("div");
      frost.className = "px-img px-frost";
      frame.appendChild(frost);
    }
    var link = document.createElement("a");
    link.className = "px-link";
    link.href = "https://www.pixiv.net/artworks/" + c.pid;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "P站 ↗";
    frame.appendChild(link);
    item.appendChild(frame);
    grid.appendChild(item);
  });
  return grid;
}
function makePxImg(src, blur, pid) {
  var img = document.createElement("img");
  img.className = "px-img " + (blur ? "px-blur" : "px-revealed");
  img.src = src;
  img.alt = "Pixiv 插画 " + pid;
  img.addEventListener("click", function () {
    if (!img.classList.contains("px-blur")) window.open(img.src, "_blank");
  });
  return img;
}
// 精选原图到达：在最近的候选墙对应位置替换为原图，加载完成后去除模糊
function revealPixivPicks(picks) {
  var grids = el.messages.querySelectorAll(".pixiv-grid");
  var grid = grids.length ? grids[grids.length - 1] : null;
  if (!grid) return;
  picks.forEach(function (p) {
    var item = grid.querySelector('.px-item[data-index="' + p.index + '"]');
    if (!item || item.classList.contains("picked")) return;
    var frame = item.querySelector(".px-frame");
    var old = frame.querySelector(".px-img");
    var img = makePxImg(p.url, true, p.pid);  // 原图加载完成前保持模糊
    function clear() {
      img.classList.remove("px-blur");
      img.classList.add("px-revealed");
      frame.classList.add("revealed");
      item.classList.add("picked");
    }
    if (img.complete && img.naturalWidth) clear();
    else img.addEventListener("load", clear);
    frame.insertBefore(img, old);
    if (old) old.remove();
  });
}
/* ==== 全屏图片查看器：放大 / 下载 / 按生图方式查看提示词 ==== */
function openImageViewer(src, meta) {
  el.ivImg.src = src;
  el.ivPanel.innerHTML = "";
  el.ivPanel.classList.remove("open");
  el.ivInfo.classList.remove("active");
  var hasMeta = !!(meta && (meta.prompt || meta.negative_prompt ||
      meta.model || meta.sampler));
  el.ivInfo.classList.toggle("hidden", !hasMeta);
  if (hasMeta) el.ivPanel.appendChild(buildImageInfoPanel(meta));
  el.imgViewer.classList.remove("hidden");
}
function closeImageViewer() {
  el.imgViewer.classList.add("hidden");
  el.ivPanel.classList.remove("open");
  el.ivInfo.classList.remove("active");
  el.ivImg.src = "";
}
function toggleImageInfo() {
  var open = el.ivPanel.classList.toggle("open");
  el.ivInfo.classList.toggle("active", open);
}
function downloadViewerImage() {
  var src = el.ivImg.src;
  if (!src) return;
  var a = document.createElement("a");
  a.href = src;
  a.download = suggestImageName(src);
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
function suggestImageName(src) {
  var dm = /^data:image\/([\w.+-]+)/i.exec(src);
  if (dm) {
    var ext = dm[1].toLowerCase().replace("jpeg", "jpg");
    return "image_" + viewerStamp() + "." + ext;
  }
  try {
    var base = String(new URL(src, location.href).pathname).split("/").pop();
    if (base && /\.[A-Za-z0-9]{2,5}$/.test(base)) return base;
  } catch (e) {}
  return "image.jpg";
}
function viewerStamp() {
  var d = new Date();
  function p(n) { return (n < 10 ? "0" : "") + n; }
  return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "_" +
    p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
}

// 信息面板：SD 与云端两种生图方式结构不同
function buildImageInfoPanel(meta) {
  var box = document.createElement("div");
  box.className = "iv-info";
  if (meta.engine === "sd") {
    box.appendChild(makeInfoHeader("🖥", "Stable Diffusion",
      meta.engine_label || "Anything V5 · 文生图"));
    if (meta.prompt) box.appendChild(makeInfoBlock("正向提示词", meta.prompt, "iv-mono"));
    if (meta.negative_prompt) {
      box.appendChild(makeInfoBlock("负向提示词", meta.negative_prompt, "iv-mono"));
    }
    var seedText = (meta.seed === -1 || meta.seed === "-1") ? "随机" : meta.seed;
    box.appendChild(makeInfoGrid([
      ["尺寸", (meta.width || "?") + " × " + (meta.height || "?")],
      ["采样步数", meta.steps],
      ["CFG Scale", meta.cfg_scale],
      ["采样器", meta.sampler],
      ["调度器", meta.schedule],
      ["种子", seedText],
    ]));
  } else if (meta.engine === "cloud") {
    var isEdit = meta.mode === "img2img";
    box.appendChild(makeInfoHeader("☁️", isEdit ? "云端图生图" : "云端生图",
      meta.model ? "模型 " + meta.model : ""));
    var chips = [];
    if (meta.model) chips.push(meta.model);
    if (meta.size) chips.push(meta.size);
    if (chips.length) box.appendChild(makeInfoChips(chips));
    if (isEdit && meta.refs && meta.refs.length) {
      box.appendChild(makeInfoRefs(meta.refs));
    }
    if (meta.prompt) box.appendChild(makeInfoBlock("提示词", meta.prompt, null));
  } else {
    box.appendChild(makeInfoHeader("🖼", "图片信息", ""));
    if (meta.prompt) box.appendChild(makeInfoBlock("提示词", meta.prompt, null));
  }
  return box;
}
function makeInfoHeader(icon, title, sub) {
  var h = document.createElement("div");
  h.className = "iv-info-head";
  h.innerHTML =
    '<span class="iv-info-icon">' + icon + "</span><div>" +
    '<div class="iv-info-title">' + escapeHtml(title) + "</div>" +
    (sub ? '<div class="iv-info-sub">' + escapeHtml(sub) + "</div>" : "") +
    "</div>";
  return h;
}
function makeInfoRefs(refs) {
  var wrap = document.createElement("div");
  wrap.className = "iv-block";
  var bar = document.createElement("div");
  bar.className = "iv-block-bar";
  var lab = document.createElement("span");
  lab.className = "iv-block-label";
  lab.textContent = "参考图片（" + refs.length + "）· 点击放大";
  bar.appendChild(lab);
  var body = document.createElement("div");
  body.className = "iv-refs";
  refs.forEach(function (url, i) {
    var fig = document.createElement("button");
    fig.type = "button";
    fig.className = "iv-ref";
    var img = document.createElement("img");
    img.loading = "lazy";
    img.src = url;
    img.alt = "图" + (i + 1);
    var cap = document.createElement("span");
    cap.className = "iv-ref-cap";
    cap.textContent = "图" + (i + 1);
    fig.appendChild(img);
    fig.appendChild(cap);
    fig.addEventListener("click", function () { openImageViewer(url); });
    body.appendChild(fig);
  });
  wrap.appendChild(bar);
  wrap.appendChild(body);
  return wrap;
}
function makeInfoBlock(label, text, extraClass) {
  var wrap = document.createElement("div");
  wrap.className = "iv-block";
  var bar = document.createElement("div");
  bar.className = "iv-block-bar";
  var lab = document.createElement("span");
  lab.className = "iv-block-label";
  lab.textContent = label;
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "iv-copy";
  btn.textContent = "复制";
  btn.addEventListener("click", function () {
    copyText(text).then(function (ok) {
      showToast(ok ? "已复制到剪贴板" : "复制失败，请手动选择", ok ? "ok" : "err");
    });
  });
  bar.appendChild(lab);
  bar.appendChild(btn);
  var body = document.createElement("div");
  body.className = "iv-block-body" + (extraClass ? " " + extraClass : "");
  body.textContent = text;
  wrap.appendChild(bar);
  wrap.appendChild(body);
  return wrap;
}
function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(
      function () { return true; },
      function () { return fallbackCopy(text); });
  }
  return Promise.resolve(fallbackCopy(text));
}
function fallbackCopy(text) {
  var ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  var ok = false;
  try { ok = document.execCommand("copy"); } catch (e) {}
  ta.remove();
  return ok;
}
function makeInfoGrid(rows) {
  var grid = document.createElement("div");
  grid.className = "iv-param-grid";
  rows.forEach(function (r) {
    if (r[1] === undefined || r[1] === null || r[1] === "") return;
    var item = document.createElement("div");
    item.className = "iv-param";
    var k = document.createElement("span");
    k.className = "iv-param-k";
    k.textContent = r[0];
    var v = document.createElement("span");
    v.className = "iv-param-v";
    v.textContent = String(r[1]);
    item.appendChild(k);
    item.appendChild(v);
    grid.appendChild(item);
  });
  return grid;
}
function makeInfoChips(chips) {
  var wrap = document.createElement("div");
  wrap.className = "iv-chips";
  chips.forEach(function (c) {
    var s = document.createElement("span");
    s.className = "iv-chip";
    s.textContent = c;
    wrap.appendChild(s);
  });
  return wrap;
}
