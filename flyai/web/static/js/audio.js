/* AI 语音气泡：识别 [语音已生成] 标记，在工具卡内渲染独立语音条。
 * 原生 <audio> 驱动：播放/暂停、进度跳转、时长、下载；页面内互斥播放。
 */
"use strict";

function extractGeneratedAudio(text) {
  var m = /\[(?:语音已生成|语音)\]\s*(\S+)/.exec(text || "");
  return m ? m[1] : "";
}

// [语音参数] 单行 JSON（仅用于卡片提示，不外显）
function extractAudioMeta(text) {
  var m = /^\[语音参数\]\s*(\{.*\})\s*$/m.exec(text || "");
  if (!m) return null;
  try { return JSON.parse(m[1]); } catch (e) { return null; }
}

function isAudioMarkerText(text) {
  return /^\[(?:语音已生成|语音)\]/.test(text || "");
}

function formatAudioTime(sec) {
  if (!isFinite(sec) || sec < 0) return "00:00";
  var total = Math.round(sec);
  var h = Math.floor(total / 3600);
  var m = Math.floor((total % 3600) / 60);
  var s = total % 60;
  var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
  if (h > 0) return h + ":" + pad(m) + ":" + pad(s);
  return pad(m) + ":" + pad(s);
}

function makeAudioCard(url, meta) {
  url = assetUrl(url);
  var card = document.createElement("div");
  card.className = "audio-card";
  if (meta && meta.speaker) {
    card.title = "AI 语音 · " + meta.speaker;
  }

  var play = document.createElement("button");
  play.className = "au-play";
  play.type = "button";
  play.setAttribute("aria-label", "播放");
  play.textContent = "▶";

  var ico = document.createElement("span");
  ico.className = "au-ico";
  ico.textContent = "🔈";

  var bar = document.createElement("div");
  bar.className = "au-bar";
  var played = document.createElement("div");
  played.className = "au-played";
  bar.appendChild(played);

  var timeLabel = document.createElement("span");
  timeLabel.className = "au-time";
  timeLabel.textContent = "00:00 / 00:00";

  var dl = document.createElement("a");
  dl.className = "au-dl";
  dl.href = url;
  dl.download = url.split("/").pop() || "tts.mp3";
  dl.textContent = "⤓";
  dl.title = "下载";
  dl.addEventListener("click", function (ev) {
    if (!(window.NativeSaver && NativeSaver.isNative())) return;
    // APK 里锚点下载不生效：fetch 回 Blob 后交原生插件存下载目录
    ev.preventDefault();
    var name = (url.split("/").pop() || "tts.mp3").split("?")[0] || "tts.mp3";
    fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.blob();
    }).then(function (blob) {
      downloadBlob(blob, name);
    }).catch(function (e) { showToast("保存语音失败: " + e.message, "err"); });
  });

  var audio = document.createElement("audio");
  audio.preload = "metadata";
  audio.src = url;

  function setPlayingUI(isPlaying) {
    play.textContent = isPlaying ? "⏸" : "▶";
    play.setAttribute("aria-label", isPlaying ? "暂停" : "播放");
    card.classList.toggle("playing", isPlaying);
  }

  function pauseOthers() {
    document.querySelectorAll(".audio-card.playing").forEach(function (other) {
      if (other !== card) {
        var otherAudio = other.querySelector("audio");
        if (otherAudio) otherAudio.pause();
      }
    });
  }

  play.addEventListener("click", function () {
    if (audio.paused) {
      pauseOthers();
      var p = audio.play();
      if (p && p.catch) {
        p.catch(function () {
          setPlayingUI(false);
          showToast("当前浏览器不允许自动播放，请再点一次", "info", 3000);
        });
      }
    } else {
      audio.pause();
    }
  });

  audio.addEventListener("play", function () { setPlayingUI(true); });
  audio.addEventListener("pause", function () { setPlayingUI(false); });

  audio.addEventListener("loadedmetadata", function () {
    timeLabel.textContent =
      formatAudioTime(audio.currentTime) + " / " + formatAudioTime(audio.duration);
  });

  audio.addEventListener("timeupdate", function () {
    if (isFinite(audio.duration) && audio.duration > 0) {
      played.style.width = (audio.currentTime / audio.duration * 100) + "%";
    }
    timeLabel.textContent =
      formatAudioTime(audio.currentTime) + " / " + formatAudioTime(audio.duration);
  });

  audio.addEventListener("ended", function () {
    setPlayingUI(false);
    played.style.width = "0%";
    audio.currentTime = 0;
  });

  audio.addEventListener("error", function () {
    card.classList.add("au-err");
    play.disabled = true;
    timeLabel.textContent = "音频加载失败";
  });

  bar.addEventListener("click", function (evt) {
    if (!isFinite(audio.duration) || audio.duration <= 0) return;
    var rect = bar.getBoundingClientRect();
    var ratio = (evt.clientX - rect.left) / rect.width;
    ratio = Math.max(0, Math.min(1, ratio));
    audio.currentTime = ratio * audio.duration;
  });

  card.appendChild(play);
  card.appendChild(ico);
  card.appendChild(bar);
  card.appendChild(timeLabel);
  card.appendChild(dl);
  card.appendChild(audio);
  return card;
}
