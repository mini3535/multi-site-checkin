const hostLabel = document.querySelector("#host");
const exportButton = document.querySelector("#export");
const status = document.querySelector("#status");

function setStatus(message, isError = false) {
  status.textContent = message;
  status.dataset.state = isError ? "error" : "ok";
}

async function currentTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab?.id || !tab.url) throw new Error("请先打开已登录的网站页面。");
  const url = new URL(tab.url);
  if (url.protocol !== "https:") throw new Error("仅支持 HTTPS 网站。");
  return { tab, url };
}

async function loadPage() {
  try {
    const { tab, url } = await currentTab();
    hostLabel.textContent = url.hostname;
    exportButton.disabled = false;
  } catch (error) {
    hostLabel.textContent = "未选择 HTTPS 网站";
    setStatus(error.message, true);
  }
}

function playwrightCookie(cookie) {
  const item = {
    name: cookie.name,
    value: cookie.value,
    domain: cookie.domain,
    path: cookie.path,
    expires: cookie.expirationDate ?? -1,
    httpOnly: cookie.httpOnly,
    secure: cookie.secure,
  };
  const sameSite = { strict: "Strict", lax: "Lax", no_restriction: "None" }[cookie.sameSite];
  if (sameSite) item.sameSite = sameSite;
  return item;
}

async function exportSession() {
  exportButton.disabled = true;
  try {
    const { tab, url } = await currentTab();
    const granted = await chrome.permissions.request({
      permissions: ["cookies"],
      origins: [url.origin + "/*"],
    });
    if (!granted) throw new Error("需要允許此網站的 Cookie 權限才能导出登录会话。");

    const [{ result: originData }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        origin: location.origin,
        localStorage: Object.keys(localStorage).map((name) => ({
          name,
          value: localStorage.getItem(name),
        })),
        sessionStorageCount: sessionStorage.length,
      }),
    });
    const cookies = await chrome.cookies.getAll({ url: tab.url });
    if (cookies.length > 500) throw new Error("此网站的 Cookie 数量过多，暂不支持导出。");
    if (!cookies.length && !originData.localStorage.length) {
      const extra = originData.sessionStorageCount
        ? "检测到 sessionStorage 登录信息，当前导入流程不支持这种存储方式。"
        : "请确认你已在当前网站登录，并打开登录后的页面。";
      throw new Error("没有找到可导出的登录会话。" + extra);
    }
    const state = {
      cookies: cookies.map(playwrightCookie),
      origins: [{ origin: originData.origin, localStorage: originData.localStorage }],
    };
    const json = JSON.stringify(state, null, 2);
    if (new TextEncoder().encode(json).length > 512 * 1024) {
      throw new Error("登录会话文件超过 512 KiB，请清理网站存储后重试。");
    }
    const blob = new Blob([json], { type: "application/json" });
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = url.hostname + "-session.json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    const warning = originData.sessionStorageCount
      ? " 注意：此网站还使用 sessionStorage，当前文件不包含它，导入后可能仍需单独适配。"
      : "";
    setStatus("会话文件已下载。接着回到签到助手上传该文件。" + warning);
  } catch (error) {
    setStatus(error.message || "导出失败，请确认网站已登录后重试。", true);
  } finally {
    exportButton.disabled = false;
  }
}

exportButton.addEventListener("click", exportSession);
loadPage();
