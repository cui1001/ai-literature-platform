/* 路由状态源：所有视图模块只读 #view[data-view]，不再各自维护 view 字段 */

export function currentView(host = document.getElementById("view")) {
  return host?.dataset?.view || "";
}

export function isView(name, host = document.getElementById("view")) {
  return currentView(host) === name;
}

export function setView(host, view) {
  if (!host) return;
  host.dataset.view = view;
}

/** 阅读器模式：优先 mode=zh|bilingual|en，兼容旧的 zh=1 */
export function readerMode(params) {
  const mode = (params?.get?.("mode") || "").toLowerCase();
  if (mode === "zh" || mode === "bilingual" || mode === "en") return mode;
  return params?.get?.("zh") === "1" ? "zh" : "en";
}

/** 生成阅读器 hash；en 模式不带参数，保持 URL 简洁 */
export function readerHash(paperId, mode) {
  const base = `#reader/${paperId}`;
  return mode === "en" ? base : `${base}?mode=${mode}`;
}

/** 只改地址栏、不触发整页重渲染（配合 popstate 使用） */
export function pushReaderMode(paperId, mode) {
  const target = readerHash(paperId, mode);
  if (location.hash !== target) history.pushState(null, "", target);
}
