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
