// [调研轮12 2026-09-16] OpenCode sidecar 预载补丁：抬高监听器上限。
//
// ## 为什么需要
// `opencode serve` 每个请求会在内部事件总线上挂一个监听器且不摘除，累积到
// EventTarget 默认上限（10）即触发：
//   MaxListenersExceededWarning: Possible EventTarget memory leak detected.
//   11 event listeners added to [yZ]. MaxListeners is undefined.
// 随后进程崩溃（实测 ~11 次请求必崩；2026-08-16 / 09-05 多次实锤）。
// 桥接层此前的对策是「每 8 次调用杀进程重启」（OPENCODE_RECYCLE_EVERY=8）——
// 代价：GLM 用量 ~780 次/24h ⇒ **每天约 97 次重建、每次 20~40s 不可用**
// （日志 `opencode server listening` 已出现 587 次），这正是「GLM 持续不稳」的根因：
// 调用一旦落在重建窗口内，就是 `WinError 10061 目标计算机积极拒绝` 或超时。
//
// ## 补丁做什么
// 把 EventTarget / EventEmitter 的默认监听器上限设为**无限制**（0=unlimited），
// 使警告与随之而来的崩溃不再发生 —— 治根，而不是靠频繁重启规避症状。
// 通过 NODE_OPTIONS=--require 预载，无需改动 opencode 本体。
//
// 若 opencode 是打包二进制导致 --require 不生效，本补丁无副作用（静默忽略）。
try {
  const events = require('events');
  if (typeof events.setMaxListeners === 'function') {
    // Node >= 15：不传 target 时为全局默认（含此后创建的 EventTarget）
    events.setMaxListeners(0);
  }
  if (events.defaultMaxListeners !== undefined) {
    events.defaultMaxListeners = 0;
  }
} catch (e) {
  // 环境不支持时静默忽略：桥接层的轮换兜底仍然有效
}
