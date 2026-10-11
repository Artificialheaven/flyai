/* BGM 引擎：FluidSynth WASM（JSSynth）+ GeneralUser GS 音色库。
 *
 * 调用链：waitForReady → AudioContext → Synthesizer.init →
 * createAudioNode(8192) → loadSFont → addSMFDataToPlayer →
 * setPlayerLoop(-1) → playPlayer。循环用 FluidSynth 播放器原生 loop
 * （进程内回绕、首尾无停顿），不再播完重新加载。
 * 中间插入 GainNode：基础音量 × TTS 压低系数。任何初始化失败 → 永久
 * 标记 unavailable，Galgame 静默降级（无音乐，其余功能照常）。
 */
"use strict";

var bgmEngine = (function () {
  var SF2_URL = "/static/vendor/GeneralUser-GS.sf2";
  // Cache API 中的独立命名空间：换音色库时升版本号即自动失效
  var SF2_CACHE = "flyai-bgm-sf2-v1";

  var synth = null;
  var ctx = null;
  var gainNode = null;
  var ensurePromise = null;
  var unavailable = false;
  var ready = false;
  var playingUrl = null;
  var loopToken = 0;
  var baseVolume = 1.0;   // 设置中的 BGM 音量
  var ducked = false;     // TTS 播放中 → 额外压低
  var audioNode = null;   // 保留引用，防 ScriptProcessor 被 GC 后播放卡死

  function lazyEnsure(onProgress) {
    if (unavailable) return Promise.reject(new Error("bgm unavailable"));
    if (ensurePromise) return ensurePromise;
    if (typeof JSSynth === "undefined") {
      unavailable = true;
      return Promise.reject(new Error("JSSynth 未加载"));
    }
    onProgress = onProgress || function () {};
    onProgress("正在初始化音乐合成器…");

    ensurePromise = JSSynth.waitForReady().then(function () {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) throw new Error("浏览器不支持 WebAudio");
      ctx = new AC();
      if (ctx.state === "suspended") return ctx.resume();
    }).then(function () {
      synth = new JSSynth.Synthesizer();
      synth.init(ctx.sampleRate);
      gainNode = ctx.createGain();
      applyGain(0);
      gainNode.connect(ctx.destination);
      audioNode = synth.createAudioNode(ctx, 8192); // ScriptProcessor：demo 同款
      audioNode.connect(gainNode);
      // onStageClick 首次点击即用户手势：申请持久化存储，授权后浏览器不会
      // 在磁盘紧张时自动回收站点数据（含音色库）；不支持/拒绝也不影响
      requestPersistentStorage();
      return loadSFontBuffer(onProgress);
    }).then(function (buf) {
      return synth.loadSFont(buf);
    }).then(function () {
      ready = true;
      onProgress("");
    }).catch(function (err) {
      // 不重试半成品：静默降级，由调用方决定是否提示
      unavailable = true;
      ensurePromise = null;
      throw err;
    });
    return ensurePromise;
  }

  // 申请持久化存储（Chrome/Edge 支持；需用户手势，首次点击正好满足）
  function requestPersistentStorage() {
    try {
      if (navigator.storage && navigator.storage.persist) {
        navigator.storage.persist().then(function (persistent) {
          if (persistent) console.info("[bgm] 存储已持久化，音色库不会被自动回收");
        }).catch(function () {});
      }
    } catch (e) {}
  }

  // 音色库优先读 Cache API（站点存储，清普通 HTTP 缓存不影响）：
  // 命中 → 本地读取；未命中 → 网络下载并写入缓存。缓存内容损坏时自动重下。
  function isValidSf2(buf) {
    // sf2 是 RIFF 容器（"RIFF" 开头），正常体积远大于 1KB。
    // 注意：ArrayBuffer 不支持下标访问，必须经 Uint8Array 视图
    if (!buf || buf.byteLength < 1024) return false;
    var b0 = new Uint8Array(buf, 0, 4);
    return String.fromCharCode(b0[0], b0[1], b0[2], b0[3]) === "RIFF";
  }

  function loadSFontBuffer(onProgress) {
    if (typeof caches === "undefined" || !caches.open) {
      onProgress("正在下载 GS 音色库（约 32MB）…");
      return fetch(SF2_URL).then(function (res) {
        if (!res.ok) throw new Error("音色库下载失败 HTTP " + res.status);
        return res.arrayBuffer();
      });
    }
    var cache = null;
    function fetchAndStore() {
      onProgress("正在下载 GS 音色库（约 32MB，仅一次）…");
      return fetch(SF2_URL).then(function (res) {
        if (!res.ok) throw new Error("音色库下载失败 HTTP " + res.status);
        // 响应体只能消费一次：clone 后缓存；put 的异步拒绝（如配额不足）要接住
        try {
          var putP = cache.put(SF2_URL, res.clone());
          if (putP && putP.catch) putP.catch(function () {});
        } catch (e) {}
        return res.arrayBuffer();
      });
    }
    return caches.open(SF2_CACHE).then(function (c) {
      cache = c;
      return cache.match(SF2_URL);
    }).then(function (hit) {
      if (!hit || !hit.ok) return fetchAndStore();
      onProgress("正在读取本地音色库…");
      return hit.arrayBuffer().catch(function () { return null; });
    }).then(function (buf) {
      if (isValidSf2(buf)) return buf;
      // 缓存损坏：删掉坏条目，重新下载一次
      try { cache.delete(SF2_URL); } catch (e) {}
      return fetchAndStore();
    }).then(function (buf) {
      if (!isValidSf2(buf)) throw new Error("音色库内容不完整");
      return buf;
    });
  }

  function fetchMidi(url) {
    return fetch(assetUrl(url)).then(function (res) {
      if (!res.ok) throw new Error("MIDI 下载失败 HTTP " + res.status);
      return res.arrayBuffer();
    });
  }

  function effectiveGain() {
    return baseVolume * (ducked ? 0.3 : 1.0);
  }

  // timeConstant 秒：0=立即（初始化前不发声），>0=平滑过渡（不爆音）
  function applyGain(timeConstant) {
    if (!gainNode) return;
    var target = effectiveGain();
    var now = ctx.currentTime;
    gainNode.gain.cancelScheduledValues(now);
    if (timeConstant > 0) gainNode.gain.setTargetAtTime(target, now, timeConstant);
    else gainNode.gain.setValueAtTime(target, now);
  }

  function play(url) {
    if (unavailable || !url) return Promise.resolve();
    return lazyEnsure().then(function () {
      stopInternal();
      return fetchMidi(url);
    }).then(function (buf) {
      playingUrl = url;
      applyGain(0.05);
      synth.closePlayer();
      return synth.addSMFDataToPlayer(buf.slice(0));
    }).then(function () {
      // FluidSynth 原生无限循环：结尾在进程内直接回绕，不重新加载、无停顿。
      // 必须在 playPlayer 之前设置
      synth.setPlayerLoop(-1);
      return synth.playPlayer();
    }).catch(function (err) {
      if (unavailable) return;
      console.warn("[bgm] 播放失败：", err);
    });
  }

  function stopInternal() {
    loopToken++;
    playingUrl = null;
    if (synth) {
      try { synth.stopPlayer(); } catch (e) {}
    }
  }

  function stop() {
    stopInternal();
    return Promise.resolve();
  }

  // TTS 期间把 BGM 额外压到 30%；结束后恢复基础音量
  function setDuck(on) {
    ducked = !!on;
    applyGain(0.15);
  }

  // 设置页 BGM 音量：v 为 0-100
  function setVolume(v) {
    baseVolume = Math.max(0, Math.min(100, Math.round(v))) / 100;
    applyGain(0.05);
  }

  function status() {
    if (unavailable) return "unavailable";
    if (playingUrl) return "playing";
    if (ready) return "ready";
    if (ensurePromise) return "loading";
    return "idle";
  }

  function currentUrl() {
    return playingUrl;
  }

  return {
    lazyEnsure: lazyEnsure,
    play: play,
    stop: stop,
    setDuck: setDuck,
    setVolume: setVolume,
    status: status,
    currentUrl: currentUrl,
  };
})();
