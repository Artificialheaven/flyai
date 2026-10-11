"""Galgame 重放纯逻辑测试：把 galgame.js 放进最小桩环境后用 node 执行断言。

重放的推进 / 选项提取 / 场景重建都是纯函数，不依赖 DOM；node 不可用时
（如部分部署环境）自动跳过。
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node 不可用，跳过重放逻辑测试"
)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_GALGAME_JS = os.path.join(_ROOT, "flyai", "web", "static", "js", "galgame.js")

# 桩环境 + 断言脚本（拼在 galgame.js 之后，同一脚本作用域内调用其函数）
_HARNESS = r"""
var state = {galgame: {
  ttsCache: {"最后": "/uploads/u/tts_last.mp3", "你好": "/u/n1.mp3"},
  charName: "女主"
}};

var bg1 = "/uploads/u/galgame/backgrounds/sid/bg1.png";
var mid1 = "/uploads/u/galgame/music/sid/m1.mid";
var msgs = [
  {role: "user", content: "开端"},
  {role: "assistant", content: "「你好」\n（挥手）", tool_calls: [
    {id: "tc_bg", name: "galgame_draw_background"},
    {id: "tc_ch1", name: "galgame_choice", arguments: {options: ["A", "B"]}}
  ]},
  {role: "tool", tool_call_id: "tc_bg", name: "galgame_draw_background",
   content: "[背景已生成] " + bg1 + "\n背景已成功生成，已自动放置到画面上。"},
  {role: "tool", tool_call_id: "tc_ch1", name: "galgame_choice",
   content: "用户选择了：「A」\n请严格依据该选择继续演出。"},
  {role: "assistant", content: "「接着说」\n旁白二", tool_calls: [
    {id: "tc_mu", name: "galgame_compose_music"},
    {id: "tc_ch2", name: "galgame_choice", arguments: {options: ["C", "D"]}}
  ]},
  {role: "tool", tool_call_id: "tc_mu", name: "galgame_compose_music",
   content: "[音乐已生成] " + mid1 + "\n音乐已成功创作，已自动开始循环播放。"},
  {role: "tool", tool_call_id: "tc_ch2", name: "galgame_choice",
   content: "用户选择了：「C」\n请继续。"},
  {role: "assistant", content: "「最后」"}
];

var fails = [];
function eq(a, b, msg) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    fails.push(msg + " got=" + JSON.stringify(a) + " want=" + JSON.stringify(b));
  }
}

eq(gvAssistantChoiceCall(msgs[1]).id, "tc_ch1", "choice call 1");
eq(gvAssistantChoiceCall(msgs[7]), null, "no choice call");
eq(gvChoiceOutcome(msgs, 1, "tc_ch1").state, "answered", "outcome A state");
eq(gvChoiceOutcome(msgs, 1, "tc_ch1").text, "A", "outcome A text");
eq(gvChoiceSelected(msgs, 1, "tc_ch1"), "A", "selected A");
eq(gvChoiceSelected(msgs, 4, "tc_ch2"), "C", "selected C");
eq(gvChoiceSelected(msgs, 1, "nope"), "", "unknown call id");
eq(gvTimeline(msgs, -1), [0, 1, 4, 7], "timeline from start");
eq(gvTimeline(msgs, 1), [4, 7], "timeline after choice");
eq(gvReplayTurns(msgs, 1), [4, 7], "replay turns");
eq(gvReplayTurns(msgs, 4), [7], "replay turns later");

var sc = replaySceneUpTo(msgs, 4);
eq(sc.background.url, bg1, "scene bg reconstructed");
eq(sc.sprite, undefined, "no sprite");
eq(sc.music, undefined, "no music yet");

var sc2 = replaySceneUpTo(msgs, 7);
eq(sc2.music.url, mid1, "music reconstructed");
eq(sc2.background.url, bg1, "bg still there");

// [场景切换] 为整体替换
var switched = parseSceneMarker(
  "[场景切换] " + JSON.stringify({background: {id: "x", url: "/bg2"},
                                 sprite: {}, music: {}}),
  sc2);
eq(switched.background.url, "/bg2", "scene switch replaces");
eq(switched.music.url, undefined, "old music dropped");

// 自动放置只在首次生效，不覆盖已有
var kept = parseSceneMarker("[背景已生成] /bg3\nblah",
                           {background: {url: "/bg2"}});
eq(kept.background.url, "/bg2", "existing bg kept");
var firstBg = parseSceneMarker("[背景已生成] /bg3", {});
eq(firstBg.background.url, "/bg3", "first bg set");

var withSp = parseSceneMarker("[立绘已生成] /sp1\nx", {});
eq(withSp.sprite.url, "/sp1", "sprite set");

// 第 4 条助理消息对应的工具结果里含音乐标记
var ts = replayApplyTurnScene(msgs, 4, {});
eq(ts.music.url, mid1, "turn music marker applied");

var segs = buildHistorySegments(msgs[7]);
eq(segs.length, 1, "last msg one segment");
eq(segs[0].type, "speech", "last is speech");
eq(segs[0].tts.url, "/uploads/u/tts_last.mp3", "tts url from cache");

var userSegs = buildHistorySegments(msgs[0]);
eq(userSegs.length, 1, "user node one segment");
eq(userSegs[0].type, "playerline", "user node is playerline");
eq(userSegs[0].display, "开端", "playerline text");
eq(userSegs[0].tts, null, "playerline has no tts");

// 全链模拟：与 startReplay / replayNextTurn / replayTurnDone 相同的推进逻辑
function simulate(startIdx) {
  var fromStart = startIdx == null;
  var turns = gvTimeline(msgs, fromStart ? -1 : startIdx);
  var scene = replaySceneUpTo(msgs, turns[0]);
  var log = [];
  turns.forEach(function (idx) {
    scene = replayApplyTurnScene(msgs, idx, scene);
    var call = gvAssistantChoiceCall(msgs[idx]);
    var oc = call ? gvChoiceOutcome(msgs, idx, call.id) : {state: "none", text: ""};
    log.push({idx: idx, state: oc.state, text: oc.text,
              bg: (scene.background || {}).url || "",
              mu: (scene.music || {}).url || ""});
  });
  return log;
}
eq(simulate(null), [
  {idx: 0, state: "none", text: "", bg: "", mu: ""},
  {idx: 1, state: "answered", text: "A", bg: bg1, mu: ""},
  {idx: 4, state: "answered", text: "C", bg: bg1, mu: mid1},
  {idx: 7, state: "none", text: "", bg: bg1, mu: mid1}
], "replay simulation from start");
eq(simulate(1), [
  {idx: 4, state: "answered", text: "C", bg: bg1, mu: mid1},
  {idx: 7, state: "none", text: "", bg: bg1, mu: mid1}
], "replay simulation from choice");

// 超时未选：工具结果为超时错误，后续剧情由 AI 自行继续
var timed = [
  {role: "assistant", content: "「等等」", tool_calls: [
    {id: "tc", name: "galgame_choice", arguments: {options: ["X", "Y"]}}
  ]},
  {role: "tool", tool_call_id: "tc", name: "galgame_choice",
   content: "[工具错误] 等待用户选择超时（600 秒）"},
  {role: "assistant", content: "「继续」"}
];
eq(gvChoiceOutcome(timed, 0, "tc").state, "timeout", "timeout outcome");
eq(gvChoiceSelected(timed, 0, "tc"), "", "timeout selected empty");
eq(gvTimeline(timed, -1), [0, 2], "timeout timeline includes both turns");
eq(gvTimeline(timed, 0), [2], "replay from timeout starts at next node");

// 工具异常：展示错误摘要，重放时可越过
var failed = [
  {role: "assistant", content: "「嗯」", tool_calls: [
    {id: "fc", name: "galgame_choice", arguments: {options: ["X", "Y"]}}
  ]},
  {role: "tool", tool_call_id: "fc", name: "galgame_choice",
   content: "[工具错误] 选项服务异常"},
  {role: "assistant", content: "「继续」"}
];
var fe = gvChoiceOutcome(failed, 0, "fc");
eq(fe.state, "error", "error outcome");
eq(fe.text, "选项服务异常", "error text");

// 未作答的选项：取不出选择、之后也没有段落
var pending = [{role: "assistant", content: "「嗯」", tool_calls: [
  {id: "pc", name: "galgame_choice", arguments: {options: ["X", "Y"]}}
]}];
eq(gvChoiceOutcome(pending, 0, "pc").state, "pending", "pending outcome");
eq(gvChoiceSelected(pending, 0, "pc"), "", "pending choice empty");
eq(gvReplayTurns(pending, 0), [], "pending has no turns after");

console.log(JSON.stringify({failures: fails}));
"""


def test_galgame_replay_logic(tmp_path):
    with open(_GALGAME_JS, "r", encoding="utf-8") as f:
        galgame_src = f.read()

    combined = os.path.join(tmp_path, "replay_harness.js")
    with open(combined, "w", encoding="utf-8") as f:
        f.write(galgame_src)
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
    assert result["failures"] == [], "重放逻辑断言失败:\n" + "\n".join(
        result["failures"]
    )
