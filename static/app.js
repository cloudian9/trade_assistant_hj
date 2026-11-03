// ETF 监测面板交互
const state = {
  data: null,
  group: "",
  type: "",
  loading: false,
  retryTimer: null,
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const TYPE_LABEL = {
  volume_surge: "放量",
  single_large_buy: "单笔大单",
  sustained_large_buy: "持续大单",
};

const TYPE_CLASS = {
  volume_surge: "badge-volume",
  single_large_buy: "badge-single",
  sustained_large_buy: "badge-sustained",
};

function fmtMoney(value) {
  const number = Number(value) || 0;
  if (Math.abs(number) >= 100_000_000) return `${(number / 100_000_000).toFixed(2)} 亿`;
  if (Math.abs(number) >= 10_000) return `${(number / 10_000).toFixed(0)} 万`;
  return `${number.toFixed(0)} 元`;
}

function fmtVolume(value) {
  const number = Number(value) || 0;
  if (Math.abs(number) >= 100_000_000) return `${(number / 100_000_000).toFixed(2)} 亿手`;
  if (Math.abs(number) >= 10_000) return `${(number / 10_000).toFixed(1)} 万手`;
  return `${number.toFixed(0)} 手`;
}

function fmtRatio(value) {
  const number = Number(value) || 0;
  return number > 0 ? `${number.toFixed(1)}x` : "--";
}

function fmtPercent(value) {
  const number = Number(value) || 0;
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function fmtStamp(value) {
  if (!value) return "--";
  const text = String(value).replace("T", " ");
  return text.length >= 16 ? text.slice(5, 16) : text;
}

function setToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(setToast.timer);
  setToast.timer = setTimeout(() => {
    toast.hidden = true;
  }, 2600);
}

function renderStatus(data) {
  const chip = document.getElementById("marketStatus");
  const status = data.status || {};
  chip.className = "status-chip";
  if (status.status === "error") {
    chip.classList.add("is-error");
    chip.textContent = "初始化失败";
  } else if (!status.ready) {
    chip.textContent = "初始化中";
  } else if (status.market === "open") {
    chip.classList.add("is-open");
    chip.textContent = "交易中";
  } else {
    chip.classList.add("is-ready");
    chip.textContent = "休市";
  }

  const update = document.getElementById("lastUpdate");
  update.textContent = status.last_update
    ? `最后更新 ${fmtStamp(status.last_update)}`
    : status.message || "最后更新 --";
  update.title = status.message || "";
}

function filteredEtfs(data) {
  return (data.etfs || []).filter((item) => !state.group || item.group_name === state.group);
}

function filteredAlerts(data) {
  return (data.alerts || []).filter((item) => {
    if (state.group && item.group_name !== state.group) return false;
    if (state.type && item.alert_type !== state.type) return false;
    return true;
  });
}

function renderKpis(data, etfs, alerts) {
  const surgeCount = etfs.filter((item) => Number(item.volume_ratio) >= 1.8).length;
  document.getElementById("kpiMonitored").textContent = etfs.length;
  document.getElementById("kpiSurge").textContent = surgeCount;
  document.getElementById("kpiAlerts").textContent = alerts.length;
  document.getElementById("kpiTicks").textContent = (data.summary || {}).big_tick_count || 0;
}

function renderRanking(etfs) {
  const container = document.getElementById("rankList");
  container.replaceChildren();
  const rows = etfs
    .filter((item) => Number(item.volume) > 0 && Number(item.volume_ratio) > 0)
    .sort((a, b) => Number(b.volume_ratio) - Number(a.volume_ratio))
    .slice(0, 6);

  if (!rows.length) {
    container.append(el("p", "rank-empty", "暂无放量数据。开盘后每分钟更新累计成交量与基线比值。"));
    return;
  }

  rows.forEach((item) => {
    const card = el("div", "rank-item");
    const top = el("div", "rank-top");
    top.append(
      el("span", "rank-name", item.name),
      el("span", "rank-code", item.code)
    );
    const ratio = Number(item.volume_ratio) || 0;
    const ratioNode = el("span", `rank-ratio${ratio >= 1.8 ? " is-hot" : ""}`, fmtRatio(ratio));
    top.append(ratioNode);

    const bar = el("div", "rank-bar");
    const fill = el("span");
    fill.style.width = `${Math.min(100, (ratio / 5) * 100).toFixed(1)}%`;
    bar.append(fill);

    const foot = el("div", "rank-foot");
    foot.append(
      el("span", "", `成交 ${fmtVolume(item.volume)}`),
      el("span", "", `成交额 ${fmtMoney(item.amount)}`)
    );

    card.append(top, bar, foot);
    container.append(card);
  });
}

function metricChip(text) {
  return el("span", "feed-metric", text);
}

function alertMetrics(alert) {
  const metrics = alert.metrics || {};
  if (alert.alert_type === "volume_surge") {
    return [
      `量比 ${fmtRatio(metrics.ratio)}`,
      `成交额 ${fmtMoney(metrics.amount)}`,
      metrics.baseline_volume ? `基线 ${fmtVolume(metrics.baseline_volume)}` : "",
    ].filter(Boolean);
  }
  if (alert.alert_type === "single_large_buy") {
    return [
      `金额 ${fmtMoney(metrics.amount)}`,
      `价格 ${Number(metrics.price || 0).toFixed(3)}`,
      `数量 ${Number(metrics.volume || 0).toFixed(0)} 股`,
    ];
  }
  if (alert.alert_type === "sustained_large_buy") {
    return [
      `${metrics.count || 0} 笔`,
      `合计 ${fmtMoney(metrics.total_amount)}`,
      `最大 ${fmtMoney(metrics.largest)}`,
    ];
  }
  return [];
}

function renderFeed(alerts) {
  const container = document.getElementById("feed");
  container.replaceChildren();

  if (!alerts.length) {
    container.append(
      el(
        "p",
        "feed-empty",
        "暂无告警。开盘后每分钟监测成交量与逐笔大单，触发放量或持续大额买入时写入这里。"
      )
    );
    return;
  }

  alerts.forEach((alert) => {
    const card = el(
      "article",
      `feed-card level-${alert.level || "info"}${alert.is_read ? " is-read" : ""}`
    );
    card.dataset.id = alert.id;

    const top = el("div", "feed-top");
    top.append(
      el("span", `badge ${TYPE_CLASS[alert.alert_type] || ""}`, TYPE_LABEL[alert.alert_type] || alert.alert_type)
    );
    if (alert.source === "review") {
      top.append(el("span", "badge badge-review", "收盘复盘"));
    }
    top.append(el("span", "feed-time", fmtStamp(alert.created_at)));
    if (!alert.is_read) top.append(el("span", "unread-dot"));

    const title = el("h3", "feed-title");
    title.append(el("span", "", alert.title));
    title.append(el("span", "feed-code", `${alert.code} · ${alert.group_name}`));

    const detail = el("p", "feed-detail", alert.detail || "");

    const metrics = el("div", "feed-metrics");
    alertMetrics(alert).forEach((text) => metrics.append(metricChip(text)));

    card.append(top, title, detail, metrics);
    card.addEventListener("click", () => markRead(alert, card));
    container.append(card);
  });
}

async function markRead(alert, card) {
  if (alert.is_read) return;
  try {
    const response = await fetch(`/api/alerts/${alert.id}/read`, { method: "POST" });
    if (!response.ok) throw new Error("mark failed");
    alert.is_read = 1;
    card.classList.add("is-read");
    const dot = card.querySelector(".unread-dot");
    if (dot) dot.remove();
  } catch (error) {
    setToast("标记已读失败，请稍后重试");
  }
}

function renderTable(etfs) {
  const body = document.getElementById("etfTableBody");
  body.replaceChildren();
  document.getElementById("etfCount").textContent = `${etfs.length} 只`;

  if (!etfs.length) {
    const row = el("tr");
    const cell = el("td", "empty-row", "暂无 ETF 数据");
    cell.colSpan = 8;
    row.append(cell);
    body.append(row);
    return;
  }

  const rows = [...etfs].sort((a, b) => Number(b.volume_ratio) - Number(a.volume_ratio));
  rows.forEach((item) => {
    const row = el("tr");

    const nameCell = el("td");
    const nameBox = el("div", "etf-name");
    nameBox.append(el("strong", "", item.name), el("span", "", item.code));
    nameCell.append(nameBox);

    const groupCell = el("td", "col-group", item.group_name);
    const priceCell = el("td", "col-price", Number(item.price || 0).toFixed(3));

    const change = Number(item.change_pct) || 0;
    const changeCell = el(
      "td",
      `col-change ${change > 0 ? "change-up" : change < 0 ? "change-down" : ""}`,
      fmtPercent(change)
    );

    const ratio = Number(item.volume_ratio) || 0;
    const ratioCell = el(
      "td",
      `ratio-value ${ratio >= 3 ? "is-hot" : ratio >= 1.8 ? "is-warm" : ""}`,
      fmtRatio(ratio)
    );

    const buyCell = el(
      "td",
      "",
      item.large_buy_count
        ? `${item.large_buy_count} 笔 / ${fmtMoney(item.large_buy_amount)}`
        : "--"
    );

    const superNet = Number(item.super_net) || 0;
    const superCell = el(
      "td",
      `col-super ${superNet > 0 ? "super-positive" : superNet < 0 ? "super-negative" : ""}`,
      superNet ? fmtMoney(superNet) : "--"
    );

    const timeCell = el("td", "col-time", fmtStamp(item.updated_at));

    row.append(
      nameCell,
      groupCell,
      priceCell,
      changeCell,
      ratioCell,
      buyCell,
      superCell,
      timeCell
    );
    body.append(row);
  });
}

function render() {
  const data = state.data;
  if (!data) return;
  renderStatus(data);
  const etfs = filteredEtfs(data);
  const alerts = filteredAlerts(data);
  renderKpis(data, etfs, alerts);
  renderRanking(etfs);
  renderFeed(alerts);
  renderTable(etfs);

  if (!data.status || !data.status.ready) {
    scheduleRetry();
  }
}

function scheduleRetry() {
  clearTimeout(state.retryTimer);
  state.retryTimer = setTimeout(() => {
    if (!document.hidden) load();
  }, 3000);
}

async function load() {
  if (state.loading) return;
  state.loading = true;
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    render();
  } catch (error) {
    const chip = document.getElementById("marketStatus");
    chip.className = "status-chip is-error";
    chip.textContent = "连接失败";
    setToast("无法连接监测服务，正在重试");
  } finally {
    state.loading = false;
  }
}

function bindFilters() {
  document.getElementById("typeTabs").addEventListener("click", (event) => {
    const button = event.target.closest(".type-tab");
    if (!button) return;
    state.type = button.dataset.type || "";
    document.querySelectorAll(".type-tab").forEach((node) => {
      node.classList.toggle("is-active", node === button);
    });
    if (state.data) render();
  });

  document.getElementById("groupSelect").addEventListener("change", (event) => {
    state.group = event.target.value;
    if (state.data) render();
  });
}

bindFilters();
load();
setInterval(() => {
  if (!document.hidden) load();
}, 15000);
