"use strict";

/*
 * Capacitor 原生壳（APK）辅助：状态栏透明叠加 + 深浅色图标切换 +
 * 硬件返回键逐级返回。
 * 普通浏览器环境下所有方法均为空操作，无需调用方判断平台。
 * 不依赖打包器：直接通过注入桥的 Capacitor.nativePromise /
 * addListener 调用原生插件（StatusBar、App）。
 */
var NativeBar = (function () {
  var inited = false;
  var lastExitAt = 0;

  function cap() {
    if (window.Capacitor && typeof window.Capacitor.isNativePlatform === "function"
        && window.Capacitor.isNativePlatform()) {
      return window.Capacitor;
    }
    return null;
  }

  function noop() {}

  /* 进入叠加模式：页面（含 Galgame 画面）铺到状态栏与挖孔区域下方 */
  function init() {
    var c = cap();
    if (!c || inited) return;
    inited = true;
    if (typeof c.nativePromise === "function") {
      c.nativePromise("StatusBar", "setOverlaysWebView", { overlay: true }).catch(noop);
      c.nativePromise("StatusBar", "setBackgroundColor", { color: "#00000000" }).catch(noop);
    }
    sync();
    initBackButton(c);
  }

  function currentTheme() {
    return document.body.className.indexOf("theme-light") >= 0 ? "light" : "dark";
  }

  /*
   * 按当前主题刷新状态栏图标颜色。
   * 插件语义：Style 描述的是背景——LIGHT=浅色背景（深色图标），
   * DARK=深色背景（浅色图标）。
   */
  function sync(theme) {
    var c = cap();
    if (!c || !inited) return;
    theme = theme || currentTheme();
    var style = theme === "light" ? "LIGHT" : "DARK";
    if (typeof c.nativePromise === "function") {
      c.nativePromise("StatusBar", "setStyle", { style: style }).catch(noop);
    }
  }

  /* ==== 返回键：按浮层 → 抽屉 → 房间视图逐级返回，顶层才退出 ==== */

  function visible(el) {
    return el && !el.classList.contains("hidden");
  }

  /* 返回 true 表示已拦截处理；false 表示已到顶层，可退出 */
  function handleBack() {
    // 1. 任意打开中的 <dialog>：关最后一个（DOM 顺序最靠后者为最上层）
    var dialogs = document.querySelectorAll("dialog[open]");
    if (dialogs.length) {
      dialogs[dialogs.length - 1].close();
      return true;
    }
    // 2. 全屏看图器
    var viewer = document.getElementById("img-viewer");
    if (visible(viewer)) { closeImageViewer(); return true; }
    // 3. 市场：先关详情；再按返回则退出市场回普通界面
    var mv = document.getElementById("market-view");
    if (visible(mv)) {
      if (visible(document.getElementById("mk-detail"))) { closeMkDetail(); return true; }
      exitMarket();
      return true;
    }
    // 4. Galgame：先关对白回看；再按返回则退出 Galgame 回普通界面
    var gv = document.getElementById("galgame-view");
    if (visible(gv)) {
      var backlog = document.getElementById("gv-backlog");
      if (visible(backlog)) { backlog.classList.add("hidden"); return true; }
      exitGalgame();
      return true;
    }
    // 4. @提及 候选框 / 更多菜单
    if (visible(document.getElementById("mention-box"))) { closeMentionBox(); return true; }
    if (visible(document.getElementById("more-menu"))) { closeMoreMenu(); return true; }
    // 5. 会话列表抽屉
    if (document.body.classList.contains("nav-open")) { closeNav(); return true; }
    // 6. 多选删除模式
    if (state && state.batchMode) { exitBatchMode(); return true; }
    // 7. 房间二级窗口列表 → 回会话第一层
    if (state && state.roomView) { backToSessions(); return true; }
    return false;
  }

  function initBackButton(c) {
    if (typeof c.addListener !== "function") return;
    c.addListener("App", "backButton", function () {
      if (handleBack()) return;
      // 顶层：2 秒内再按一次才退出，避免误触
      var now = Date.now();
      if (now - lastExitAt < 2000) {
        if (typeof c.nativeCallback === "function") c.nativeCallback("App", "exitApp", {});
        return;
      }
      lastExitAt = now;
      showToast("再按一次返回键退出", null, 2000);
    });
  }

  return {
    init: init,
    sync: sync,
  };
})();

/*
 * APK 文件保存：Android WebView 不接管 blob: 链接的下载（a[download] 点击静默丢弃），
 * 经自研 FlySaver 原生插件把文件写入系统「下载」目录（MediaStore，免权限）。
 * 浏览器环境 isNative() 为 false，调用方走原来的 a.click() 回退。
 */
var NativeSaver = (function () {
  function cap() {
    if (window.Capacitor && typeof window.Capacitor.isNativePlatform === "function"
        && window.Capacitor.isNativePlatform()) {
      return window.Capacitor;
    }
    return null;
  }

  function isNative() { return !!cap(); }

  /* Blob → dataURL → 原生 MediaStore；非原生平台返回 null（调用方自行回退） */
  function saveBlob(blob, filename) {
    var c = cap();
    if (!c || typeof c.nativePromise !== "function") return Promise.resolve(null);
    return new Promise(function (resolve, reject) {
      var fr = new FileReader();
      fr.onload = function () {
        var dataUrl = String(fr.result || "");
        var comma = dataUrl.indexOf(",");
        var data = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
        c.nativePromise("FlySaver", "save", {
          name: filename,
          mime: blob.type || "application/octet-stream",
          data: data,
        }).then(function (r) {
          resolve((r && r.name) || filename);
        }, function (e) {
          reject(new Error(typeof e === "string" ? e : (e && e.message) || "原生保存失败"));
        });
      };
      fr.onerror = function () { reject(new Error("读取 Blob 失败")); };
      fr.readAsDataURL(blob);
    });
  }

  return { isNative: isNative, saveBlob: saveBlob };
})();

/*
 * APK 本地通知：AI 在后台回答完毕时提醒用户（纯本地通知，不经过服务端推送）。
 * 通知由自研 FlyNotify 原生插件发出（支持角色头像大图标）；Android 13+ 的
 * 运行时权限弹窗借用 @capacitor/local-notifications 插件触发（授权对全应用生效）。
 * 浏览器环境为空操作。经注入桥的 nativePromise 调用，无需打包器。
 */
var NativeNotify = (function () {
  function cap() {
    if (window.Capacitor && typeof window.Capacitor.isNativePlatform === "function"
        && window.Capacitor.isNativePlatform()
        && typeof window.Capacitor.nativePromise === "function") {
      return window.Capacitor;
    }
    return null;
  }

  var asked = false;  // 本次运行期只主动弹一次授权框，被拒后不反复打扰

  /* 首次发消息时请求通知权限；已授权/已拒绝/非原生均静默 */
  function ensurePermission() {
    var c = cap();
    if (!c || asked) return;
    asked = true;
    c.nativePromise("LocalNotifications", "checkPermissions", {}).then(function (r) {
      if (r && r.display === "prompt") {
        return c.nativePromise("LocalNotifications", "requestPermissions", {});
      }
    }).catch(function () {});
  }

  /* 发一条立刻展示的通知；iconUrl 为角色头像绝对地址，留空用默认图标；
   * 点击通知由系统默认行为拉回 App */
  function notify(title, body, iconUrl) {
    var c = cap();
    if (!c) return Promise.resolve(false);
    return c.nativePromise("FlyNotify", "notify", {
      id: (Date.now() % 2147483647),
      title: String(title || "FlyAI"),
      body: String(body || ""),
      iconUrl: iconUrl || "",
    }).then(function (r) { return !!(r && r.ok); })
      .catch(function () { return false; });
  }

  /* 后台兜底：原生线程轮询 run 状态，完成即推送（socket 在后台被回收时靠它） */
  function watchRun(o) {
    var c = cap();
    if (!c) return Promise.resolve(false);
    return c.nativePromise("FlyNotify", "watchRun", {
      runId: String(o.runId || ""),
      statusUrl: String(o.statusUrl || ""),
      token: String(o.token || ""),
      title: String(o.title || "FlyAI"),
      body: String(o.body || "回答已完成，点击查看"),
      iconUrl: o.iconUrl || "",
      id: (Date.now() % 2147483647),
    }).then(function () { return true; }).catch(function () { return false; });
  }

  function cancelWatch() {
    var c = cap();
    if (c) c.nativePromise("FlyNotify", "cancelWatch", {}).catch(function () {});
  }

  return { ensurePermission: ensurePermission, notify: notify, watchRun: watchRun, cancelWatch: cancelWatch };
})();
