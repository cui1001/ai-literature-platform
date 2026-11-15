/* 长任务中心：翻译 / 导出 / 入库等任务与视图解耦，切走视图后继续运行，回来可续显进度 */

const tasks = new Map();
const listeners = new Set();

function emit() {
  const snapshot = listTasks();
  listeners.forEach((fn) => {
    try {
      fn(snapshot);
    } catch {
      /* 订阅方异常不影响任务本身 */
    }
  });
}

export function startTask(id, patch = {}) {
  tasks.set(id, {
    id,
    kind: patch.kind || "task",
    label: patch.label || id,
    detail: patch.detail || "进行中…",
    progress: patch.progress ?? 0,
    status: "running",
    error: "",
    startedAt: Date.now(),
    finishedAt: 0,
  });
  emit();
}

export function updateTask(id, patch = {}) {
  const task = tasks.get(id);
  if (!task) return;
  Object.assign(task, patch);
  emit();
}

export function finishTask(id, status = "done", patch = {}) {
  const task = tasks.get(id);
  if (!task) return;
  Object.assign(task, patch, { status, finishedAt: Date.now() });
  if (status === "done") task.progress = 100;
  emit();
}

export function getTask(id) {
  return tasks.get(id) || null;
}

export function listTasks() {
  return [...tasks.values()].sort((a, b) => b.startedAt - a.startedAt);
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function runningCount() {
  return listTasks().filter((task) => task.status === "running").length;
}
