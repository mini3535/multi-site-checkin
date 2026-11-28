const hostLabel = document.querySelector("#host");
const connectButton = document.querySelector("#connect");
const exportButton = document.querySelector("#export");
const status = document.querySelector("#status");
const pairingLink = document.querySelector("#pairing-link");
const destination = document.querySelector("#destination");
let destinationUrl = null;
let pairingCode = "";
let connectInProgress = false;
let activePage = null;

function setStatus(message, isError = false) {
  status.textContent = message;
  status.dataset.state = isError ? "error" : "ok";
}

async function currentTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab?.id || !tab.url) throw new Error("请先打开已登录的网站页面。");
  const url = new URL(tab.url);
  if (url.protocol !== "https:") throw new Error("请切换到已登录的 HTTPS 网站标签页后重试。");
  return { tab, url };
}

async function loadPage() {
  try {
    const { tab, url } = await currentTab();
    activePage = {tab, url};
    hostLabel.textContent = url.hostname;
    exportButton.disabled = false;
    updatePairingLink();
  } catch (error) {
    activePage = null;
    hostLabel.textContent = "请先打开已登录的网站";
    setStatus(error.message, true);
  }
}

function updatePairingLink() {
  if (connectInProgress) return;
  destinationUrl = null;
  pairingCode = "";
  destination.hidden = true;
  connectButton.disabled = true;
  connectButton.textContent = "识别网页并连接账号";
  try {
    const link = new URL(pairingLink.value.trim());
    const fragment = new URLSearchParams(link.hash.slice(1));
    const code = fragment.get("signin-pairing") || "";
    const loopbackAssistant = link.protocol === "http:"
      && ["localhost", "127.0.0.1"].includes(link.hostname);
    if ((link.protocol !== "https:" && !loopbackAssistant) || link.username || link.password
        || link.pathname !== "/" || link.search || !/^[A-Za-z0-9_-]{8,32}\.[A-Za-z0-9_-]{20,64}$/.test(code)) {
      throw new Error("请粘贴签到助手生成的 HTTPS 链接；HTTP 仅支持本机 localhost 或 127.0.0.1。");
    }
    destinationUrl = new URL("/api/browser-session-pairings/complete", link.origin);
    pairingCode = code;
    destination.textContent = "登录会话将发送到：" + link.origin + "。请确认这是你自己的签到助手地址。";
    destination.hidden = false;
    connectButton.disabled = !activePage;
  } catch (error) {
    destination.textContent = error instanceof TypeError
      ? "链接无效。请粘贴签到助手生成的完整连接链接。" : error.message;
    destination.hidden = !pairingLink.value.trim();
  }
}

function pageHints(tab) {
  return chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const visible = (element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden"
          && style.display !== "none" && style.opacity !== "0"
          && !element.closest('[aria-hidden="true"]');
      };
      const alreadyDonePattern = /(今日|今天).{0,12}(?:已经|已)(?:完成)?(?:签到|打卡)|(?:已经|已)(?:签到|打卡)|already.{0,20}(?:checked|signed|claim)|(?:checked|signed)\s+in.{0,16}already/i;
      const successPattern = /(?:签到|打卡|领取).{0,8}(?:成功|完成)|(?:成功|完成).{0,8}(?:签到|打卡|领取)|check[\s-]?in.{0,12}(?:success|complete)|(?:success|complete).{0,12}check[\s-]?in|claim.{0,12}(?:success|complete)/i;
      const buttonTexts = [...document.querySelectorAll("button,[role=button],input[type=button],input[type=submit]")]
        .filter(visible)
        .map((element) => (element.innerText || element.value || element.getAttribute("aria-label") || "").trim())
        .filter((text) => text.length <= 80 && /(签到|打卡|check\s?-?in|daily|claim|领取)/i.test(text)
          && !/(记录|规则|说明|攻略|教程|帮助|查看)/.test(text)
          && !alreadyDonePattern.test(text) && !successPattern.test(text))
        .sort((left, right) => {
          const score = (text) => /(签到|打卡|check\s?-?in)/i.test(text) ? 3 : /daily/i.test(text) ? 2 : 1;
          return score(right) - score(left);
        })
        .slice(0, 20);
      const successTexts = [];
      const alreadyDoneTexts = [];
      (document.body?.innerText || "").split(/\n+/)
        .map((text) => text.trim())
        .filter((text) => text.length > 0 && text.length <= 160)
        .forEach((text) => {
          if (alreadyDonePattern.test(text)) alreadyDoneTexts.push(text);
          else if (successPattern.test(text)) successTexts.push(text);
        });
      const resourceHosts = [...new Set(performance.getEntriesByType("resource")
        .slice(-200)
        .map((entry) => {
          try { return new URL(entry.name).hostname; } catch { return ""; }
        })
        .filter(Boolean))].slice(0, 100);
      return {
        pageTitle: document.title || "",
        buttonTexts: [...new Set(buttonTexts)].slice(0, 20),
        successTexts: [...new Set(successTexts)].slice(0, 20),
        alreadyDoneTexts: [...new Set(alreadyDoneTexts)].slice(0, 20),
        resourceHosts,
      };
    },
  }).then((items) => items[0]?.result || {buttonTexts: [], successTexts: [], alreadyDoneTexts: []});
}

async function connectSession() {
  if (connectInProgress) return;
  connectInProgress = true;
  connectButton.disabled = true;
  pairingLink.disabled = true;
  try {
    if (!destinationUrl || !pairingCode) throw new Error("请先粘贴有效的一次性连接链接。");
    if (!activePage) throw new Error("请在已登录的目标网站标签页重新打开扩展。");
    const {tab, url} = activePage;
    const service = new URL(pairingLink.value.trim());
    if (url.origin === service.origin) throw new Error("当前打开的是签到助手。请切回已登录的目标网站标签页，再打开扩展连接。");
    const permissionPattern = (url) => url.protocol + "//" + url.hostname + "/*";
    const granted = await chrome.permissions.request({
      permissions: ["cookies"],
      origins: [permissionPattern(url), permissionPattern(service)],
    });
    if (!granted) throw new Error("需要允许读取当前网站会话并连接你指定的签到助手。");
    const current = await currentTab();
    if (current.tab.id !== tab.id || current.url.href !== url.href) {
      throw new Error("网页已切换，请在目标网站签到页重新打开扩展后连接。");
    }

    setStatus("正在识别当前网页并读取登录状态…");
    const [pageData, cookies, hints] = await Promise.all([
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => ({
          origin: location.origin,
          localStorage: Object.keys(localStorage).map((name) => ({name, value: localStorage.getItem(name)})),
          sessionStorageCount: sessionStorage.length,
        }),
      }).then((items) => items[0]?.result),
      chrome.cookies.getAll({url: tab.url}),
      pageHints(tab),
    ]);
    if (!pageData || pageData.origin !== url.origin) throw new Error("网页已切换或无法读取，请在目标网站签到页重新打开扩展后连接。");
    if (cookies.length > 500 || pageData.localStorage.length > 500) throw new Error("此网站的登录数据过多，暂不支持自动连接。");
    if (!cookies.length && !pageData.localStorage.length) {
      const reason = pageData.sessionStorageCount
        ? "此网站将登录状态保存在 sessionStorage，当前扩展无法安全导入。"
        : "当前页面没有可用的登录状态，请先登录网站后重试。";
      throw new Error(reason);
    }
    const state = {
      cookies: cookies.map(playwrightCookie),
      origins: [{origin: pageData.origin, localStorage: pageData.localStorage}],
    };
    const size = new TextEncoder().encode(JSON.stringify(state)).length;
    if (size > 512 * 1024) throw new Error("登录状态超过 512 KiB，请清理网站存储后重试。");

    setStatus((service.protocol === "https:" ? "正在通过 HTTPS 发送登录状态到 " : "正在通过本机连接发送登录状态到 ")
      + service.hostname + "…");
    const response = await fetch(destinationUrl.href, {
      method: "POST",
      headers: {"Content-Type": "application/json", Accept: "application/json"},
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(30000),
      body: JSON.stringify({code: pairingCode, page_url: tab.url, page_title: hints.pageTitle,
        storage_state: state,
        hints: {button_texts: hints.buttonTexts, success_texts: hints.successTexts,
          already_done_texts: hints.alreadyDoneTexts, resource_hosts: hints.resourceHosts}}),
    }).catch(() => {
      throw new Error("无法连接签到助手。请确认助手地址能在这个浏览器中打开，再生成新链接重试。");
    });
    const payload = await response.json();
    if (!response.ok || payload.error) throw new Error(payload.error || "连接失败，请回签到助手重新生成连接码。");
    pairingCode = "";
    if (payload.task_created) {
      setStatus("识别完成，签到任务已自动添加并保持暂停。请回到签到助手验证规则。");
      connectButton.textContent = "已自动添加";
    } else {
      setStatus("登录状态已连接。可以关闭此弹窗，回到签到助手继续配置规则。");
      connectButton.textContent = "已连接";
    }
  } catch (error) {
    setStatus(error.message || "连接失败，请确认链接和登录状态后重试。", true);
  } finally {
    connectInProgress = false;
    pairingLink.disabled = false;
    connectButton.disabled = !activePage || !destinationUrl || !pairingCode;
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
pairingLink.addEventListener("input", updatePairingLink);
connectButton.addEventListener("click", connectSession);
loadPage();
