/* FlyAI 前端 · 头像裁剪（方形选框，输出 512×512；纯前端，复用 /api/upload）。
 * 入口：openAvatarCropper(file, onCropped(blob, ext))。经典脚本，函数挂全局作用域。 */

"use strict";

var CROP_OUTPUT = 512;

function openAvatarCropper(file, onCropped) {
  var dlg = document.getElementById("crop-dialog");
  var viewport = document.getElementById("crop-viewport");
  var img = document.getElementById("crop-img");
  var zoomSlider = document.getElementById("crop-zoom");

  var objectUrl = URL.createObjectURL(file);
  var natW = 0, natH = 0;
  var crop = { x: 0, y: 0, s: 0 };
  var sMin = 0, sMax = 0;
  var viewSize = 0;

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function viewportScale() { return viewSize / crop.s; }

  function viewToSource(vx, vy) {
    var k = viewportScale();
    return {
      x: crop.x + crop.s / 2 + (vx - viewSize / 2) / k,
      y: crop.y + crop.s / 2 + (vy - viewSize / 2) / k,
    };
  }

  function clampCrop() {
    crop.x = clamp(crop.x, 0, natW - crop.s);
    crop.y = clamp(crop.y, 0, natH - crop.s);
  }

  function render() {
    var k = viewportScale();
    img.style.width = natW * k + "px";
    img.style.height = natH * k + "px";
    img.style.left = viewSize / 2 - (crop.x + crop.s / 2) * k + "px";
    img.style.top = viewSize / 2 - (crop.y + crop.s / 2) * k + "px";
    if (sMax > sMin) {
      /* 滑块向右 = 放大（裁剪区域变小）*/
      zoomSlider.value = String(Math.round(
        (sMax - crop.s) / (sMax - sMin) * 100));
    }
  }

  /* 缩放：newS 新边长；锚点 (ax, ay) 为源图像坐标，缩放后保持在原位 */
  function zoomTo(newS, ax, ay) {
    if (ax == null) { ax = crop.x + crop.s / 2; ay = crop.y + crop.s / 2; }
    newS = clamp(newS, sMin, sMax);
    var rx = (ax - crop.x) / crop.s;
    var ry = (ay - crop.y) / crop.s;
    crop.s = newS;
    crop.x = ax - rx * newS;
    crop.y = ay - ry * newS;
    clampCrop();
    render();
  }

  /* ==== 指针手势：单指拖动 / 双指捏合 ==== */
  var pointers = {};
  var panLast = null;
  var pinch = null;

  function localPos(ev) {
    var r = viewport.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  function onPointerDown(ev) {
    viewport.setPointerCapture(ev.pointerId);
    pointers[ev.pointerId] = localPos(ev);
    var ids = Object.keys(pointers);
    if (ids.length === 1) {
      panLast = pointers[ev.pointerId];
    } else if (ids.length === 2) {
      var a = pointers[ids[0]], b = pointers[ids[1]];
      var mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      var anchor = viewToSource(mid.x, mid.y);
      pinch = {
        dist: Math.hypot(b.x - a.x, b.y - a.y),
        s: crop.s, anchor: anchor,
      };
      panLast = null;
    }
    ev.preventDefault();
  }

  function onPointerMove(ev) {
    if (!(ev.pointerId in pointers)) return;
    pointers[ev.pointerId] = localPos(ev);
    var ids = Object.keys(pointers);
    if (ids.length >= 2 && pinch) {
      var a = pointers[ids[0]], b = pointers[ids[1]];
      var d = Math.hypot(b.x - a.x, b.y - a.y);
      if (pinch.dist > 0 && d > 0) {
        zoomTo(pinch.s * d / pinch.dist, pinch.anchor.x, pinch.anchor.y);
      }
    } else if (ids.length === 1 && panLast) {
      var pos = pointers[ids[0]];
      var k = viewportScale();
      crop.x -= (pos.x - panLast.x) / k;
      crop.y -= (pos.y - panLast.y) / k;
      panLast = pos;
      clampCrop();
      render();
    }
    ev.preventDefault();
  }

  function onPointerUp(ev) {
    delete pointers[ev.pointerId];
    var ids = Object.keys(pointers);
    if (ids.length === 1) {
      panLast = pointers[ids[0]];  // 捏合结束转拖动，避免跳变
      pinch = null;
    } else if (!ids.length) {
      panLast = null; pinch = null;
    }
  }

  /* 鼠标滚轮缩放（以指针位置为锚点）*/
  function onWheel(ev) {
    ev.preventDefault();
    var pos = localPos(ev);
    var anchor = viewToSource(pos.x, pos.y);
    /* 滚轮向上（deltaY 负）= 放大：裁剪区域收缩 */
    var factor = Math.exp(ev.deltaY * 0.0015);
    zoomTo(crop.s * factor, anchor.x, anchor.y);
  }

  function measureView() {
    viewSize = viewport.getBoundingClientRect().width;
  }

  function cleanup() {
    URL.revokeObjectURL(objectUrl);
    dlg.removeEventListener("cancel", cleanup);
    viewport.removeEventListener("pointerdown", onPointerDown);
    viewport.removeEventListener("pointermove", onPointerMove);
    viewport.removeEventListener("pointerup", onPointerUp);
    viewport.removeEventListener("pointercancel", onPointerUp);
    viewport.removeEventListener("wheel", onWheel);
  }

  img.onload = function () {
    natW = img.naturalWidth;
    natH = img.naturalHeight;
    measureView();
    sMax = Math.min(natW, natH);
    sMin = Math.max(1, sMax * 0.12);
    crop.s = sMax;
    crop.x = (natW - sMax) / 2;
    crop.y = (natH - sMax) / 2;
    zoomSlider.value = "0";
    render();
  };

  viewport.addEventListener("pointerdown", onPointerDown);
  viewport.addEventListener("pointermove", onPointerMove);
  viewport.addEventListener("pointerup", onPointerUp);
  viewport.addEventListener("pointercancel", onPointerUp);
  viewport.addEventListener("wheel", onWheel, { passive: false });
  zoomSlider.addEventListener("input", function () {
    var t = parseInt(zoomSlider.value, 10) / 100;
    zoomTo(sMax - t * (sMax - sMin));
  });
  window.addEventListener("resize", function () { measureView(); render(); });

  document.getElementById("btn-crop-cancel").onclick = function () {
    cleanup(); dlg.close();
  };
  document.getElementById("btn-crop-ok").onclick = function () {
    var canvas = document.createElement("canvas");
    canvas.width = CROP_OUTPUT;
    canvas.height = CROP_OUTPUT;
    var ctx = canvas.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, crop.x, crop.y, crop.s, crop.s,
                  0, 0, CROP_OUTPUT, CROP_OUTPUT);
    /* 保留 PNG 透明通道；其余统一高质量 JPEG */
    var isPng = (file.type === "image/png");
    var type = isPng ? "image/png" : "image/jpeg";
    canvas.toBlob(function (blob) {
      if (!blob) { showToast("裁剪失败", "err"); return; }
      cleanup();
      dlg.close();
      onCropped(blob, isPng ? ".png" : ".jpg");
    }, type, 0.92);
  };

  dlg.addEventListener("cancel", cleanup);

  img.src = objectUrl;
  dlg.showModal();
}
