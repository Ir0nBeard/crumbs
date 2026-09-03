/**
 * Crumbs Journey Viewer — content script (injected ONLY on user-opted-in sites).
 *
 * Reads what a page script can legitimately see:
 *   - the JS-visible mirror cookie `crumbs_jr` (the HttpOnly __Host-crumbs_j
 *     cookie is deliberately JS-unreadable — the merchant server reads it)
 *   - localStorage keys written by the Crumbs SDK (crumbs:receipt)
 *   - heuristic agent signals (UA + WebMCP modelContext presence)
 * Reports the state to the popup via chrome.runtime messages. The extension
 * never POSTs anything by itself — verification is user-triggered in the popup.
 */
(() => {
  const MIRROR_COOKIE = "crumbs_jr";
  const RECEIPT_KEY = "crumbs:receipt";

  function getCookieValue(name) {
    const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : null;
  }

  function detectAgentSignals() {
    const ua = navigator.userAgent || "";
    const patterns = [/chatgpt/i, /gptbot/i, /claude/i, /anthropic/i, /gemini/i,
      /perplexity/i, /auto[-_ ]?browse/i, /openai/i];
    const hits = patterns.filter((re) => re.test(ua)).map((re) => re.source);
    const webmcp = !!(document.modelContext || navigator.modelContext);
    return { userAgentHits: hits, webmcp, agentLike: hits.length > 0 || webmcp };
  }

  function collect() {
    let receipt = null;
    try {
      const raw = localStorage.getItem(RECEIPT_KEY);
      if (raw) receipt = JSON.parse(raw);
    } catch (e) {
      /* localStorage unavailable — fine */
    }
    return {
      url: location.href,
      origin: location.origin,
      mirrorCookie: getCookieValue(MIRROR_COOKIE),
      receipt: receipt ? { rid: receipt.rid, jid: receipt.jid, aid: receipt.aid, exp: receipt.exp } : null,
      agentSignals: detectAgentSignals(),
      hasWebmcpTool: !!(document.modelContext || navigator.modelContext) && !!(
        (document.modelContext && document.modelContext.registerTool) ||
        (navigator.modelContext && navigator.modelContext.registerTool)
      ),
    };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === "crumbs_collect") {
      sendResponse({ ok: true, state: collect() });
    }
    return false;
  });
})();
