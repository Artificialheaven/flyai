"""Enter 发送偏好纯逻辑测试：把 ui.js 放进最小桩环境后用 node 执行断言。

手机端检测 / 默认值 / localStorage 覆盖都是纯函数，不依赖真实 DOM；
node 不可用（如部分部署环境）时自动跳过。
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node 不可用，跳过 Enter 发送逻辑测试"
)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_UI_JS = os.path.join(_ROOT, "flyai", "web", "static", "js", "ui.js")

# 桩环境 + 断言脚本（拼在 ui.js 之后，同一脚本作用域内调用其函数）
_HARNESS = r"""
var store = {};
var localStorage = {
  getItem: function (k) {
    return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
  },
  setItem: function (k, v) { store[k] = String(v); },
  removeItem: function (k) { delete store[k]; }
};
var coarse = false;
var navigator = {userAgent: "", maxTouchPoints: 0};
var window = {
  matchMedia: function (q) {
    return {matches: coarse && q === "(pointer: coarse)"};
  }
};

var fails = [];
function eq(a, b, msg) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    fails.push(msg + " got=" + JSON.stringify(a) + " want=" + JSON.stringify(b));
  }
}

var DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0";
var ANDROID_UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit Mobile";
var IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile";
var IPAD_DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit";

function use(ua, points, isCoarse) {
  navigator.userAgent = ua;
  navigator.maxTouchPoints = points;
  coarse = isCoarse;
}
function useFresh(ua, points, isCoarse) {
  store = {};
  use(ua, points, isCoarse);
}

// 桌面端：未设置过偏好时默认 Enter 发送
useFresh(DESKTOP_UA, 0, false);
eq(isMobileDevice(), false, "desktop not mobile");
eq(enterToSendEnabled(), true, "desktop default enter send on");

// 安卓 / iPhone：默认手机端、Enter 发送关闭（Enter 用来换行）
useFresh(ANDROID_UA, 5, true);
eq(isMobileDevice(), true, "android mobile");
eq(enterToSendEnabled(), false, "android default enter send off");

useFresh(IPHONE_UA, 5, true);
eq(isMobileDevice(), true, "iphone mobile");
eq(enterToSendEnabled(), false, "iphone default enter send off");

// iPadOS 桌面 UA + 粗指针触屏：识别为手机端
useFresh(IPAD_DESKTOP_UA, 5, true);
eq(isMobileDevice(), true, "ipad desktop UA detected via coarse pointer");
eq(enterToSendEnabled(), false, "ipad default enter send off");

// 同样的桌面 UA 但非触屏：仍是桌面
useFresh(IPAD_DESKTOP_UA, 0, false);
eq(isMobileDevice(), false, "desktop UA no touch not mobile");

// 显式偏好覆盖平台默认
useFresh(DESKTOP_UA, 0, false);
localStorage.setItem("flyai-enter-send", "0");
eq(enterToSendEnabled(), false, "explicit off overrides desktop default");

useFresh(ANDROID_UA, 5, true);
localStorage.setItem("flyai-enter-send", "1");
eq(enterToSendEnabled(), true, "explicit on overrides mobile default");

// 损坏 / 未知的值：回落到平台默认
useFresh(DESKTOP_UA, 0, false);
localStorage.setItem("flyai-enter-send", "yes");
eq(enterToSendEnabled(), true, "garbage value falls back to desktop default");
useFresh(ANDROID_UA, 5, true);
localStorage.setItem("flyai-enter-send", "");
eq(enterToSendEnabled(), false, "empty value falls back to mobile default");

console.log(JSON.stringify({failures: fails}));
"""


def test_enter_send_logic(tmp_path):
    with open(_UI_JS, "r", encoding="utf-8") as f:
        ui_src = f.read()

    combined = os.path.join(tmp_path, "enter_send_harness.js")
    with open(combined, "w", encoding="utf-8") as f:
        f.write(ui_src)
        f.write("\n")
        f.write(_HARNESS)

    proc = subprocess.run(
        ["node", combined],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        pytest.fail("node 执行失败:\n" + proc.stdout + "\n" + proc.stderr)

    last_line = proc.stdout.strip().splitlines()[-1]
    result = json.loads(last_line)
    assert result["failures"] == [], "Enter 发送逻辑断言失败:\n" + "\n".join(
        result["failures"]
    )
