/* 极简 DOM / 提示工具 */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function toast(message, kind = "") {
  const host = document.getElementById("toast");
  if (!host) return;
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => node.remove(), kind === "err" ? 6500 : 3200);
}

export function openModal(title, bodyHtml) {
  const dialog = document.getElementById("modal");
  dialog.querySelector(".modal-head h3").textContent = title;
  dialog.querySelector(".modal-body").innerHTML = bodyHtml;
  if (!dialog.open) dialog.showModal();
  return dialog;
}

export const closeModal = () => {
  const dialog = document.getElementById("modal");
  if (dialog.open) dialog.close();
};

export function setBusy(node, busy, label = "处理中…") {
  if (!node) return;
  if (busy) {
    node.dataset.prev = node.innerHTML;
    node.disabled = true;
    node.innerHTML = `<span class="spinner"></span> ${escapeHtml(label)}`;
  } else {
    node.disabled = false;
    if (node.dataset.prev) node.innerHTML = node.dataset.prev;
  }
}

export function fmtNumber(value) {
  const num = Number(value ?? 0);
  if (Number.isFinite(num)) return num.toLocaleString("en-US");
  return "0";
}

export function pct(value, digits = 0) {
  return `${(Number(value || 0) * 100).toFixed(digits)}%`;
}

export function shortAuthors(list, max = 3) {
  if (!Array.isArray(list) || !list.length) return "未知作者";
  const shown = list.slice(0, max).join(", ");
  return list.length > max ? `${shown} 等 ${list.length} 人` : shown;
}

export function debounce(fn, wait = 260) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
