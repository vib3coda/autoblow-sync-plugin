(function () {
  "use strict";

  // ---------- Logging ----------
  const DEBUG = localStorage.getItem("ab-debug") === "1";
  const log  = DEBUG ? console.log.bind(console, "[AB]")  : () => {};
  const warn = console.warn.bind(console, "[AB]");
  const err  = console.error.bind(console, "[AB]");

  const WS_URL = `ws://${location.hostname}:7879`;
  const RECONNECT_MS = 3000;
  const SEEK_DEBOUNCE_MS = 300;

  let ws = null;
  let wsReady = false;
  const pendingCallbacks = {};

  let lastSceneId = null;
  let lastSeekSent = 0;


  // ---------- WebSocket ----------
  function connectWS() {
    if (!wsReady) {
      const txt = document.querySelector("#ab-toolbar .ab-status-text");
      if (txt) {
        txt.textContent = "starting backend";
        txt.dataset.custom = "1";
      }
    }
    ws = new WebSocket(WS_URL);

    ws.addEventListener("open", () => {
      wsReady = true;
      log("WebSocket connected");
      const txt = document.querySelector("#ab-toolbar .ab-status-text");
      const wasCustom = txt && txt.dataset.custom === "1";
      const delay = wasCustom ? 600 : 0;
      setTimeout(() => {
        setLed("idle");
        if (txt) delete txt.dataset.custom;
        sendMsg("status").then(s => applyStatus(s));
        checkScene();
      }, delay);
    });


    ws.addEventListener("close", () => {
      wsReady = false;
      setLed("error");
      const txt = document.querySelector("#ab-toolbar .ab-status-text");
      if (txt) {
        txt.textContent = "backend stopped";
        txt.dataset.custom = "1";
      }
      warn("WS disconnected, reconnecting in", RECONNECT_MS, "ms");
      setTimeout(connectWS, RECONNECT_MS);
    });

    ws.addEventListener("error", () => setLed("error"));


    ws.addEventListener("message", e => {
      try {
        const data = JSON.parse(e.data);
        if (!(data.ok === true && data.action === "status")) {
          log("Message:", data);
        }

        if (data.action === "info" && data.text) {
          showInfo(data.text);
          return;
        }
        if (data.action === "script_ready") {
          const txt = document.querySelector("#ab-toolbar .ab-status-text");
          if (txt) {
            clearTimeout(txt._customTimer);
            txt.textContent = "script ready";
            txt.dataset.custom = "1";
            txt._customTimer = setTimeout(() => {
              delete txt.dataset.custom;
              const video = document.querySelector("video");
              if (video && !video.paused) {
                const time_ms = Math.floor(video.currentTime * 1000);
                log("script_ready → video_play @", time_ms);
                sendFire("video_play", { time_ms });
              } else {
                sendMsg("status").then(s => applyStatus(s));
              }
            }, 1200);
          }
          return;
        }




        if (data.action && pendingCallbacks[data.action]) {
          pendingCallbacks[data.action](data);
          delete pendingCallbacks[data.action];
        }

        if (typeof data.enabled === "boolean" || typeof data.armed === "boolean") {
          applyStatus(data);
        }

        if (data.type === "status_update") {
          applyStatus(data);
        }
      } catch (err) {
        warn("Parse error:", err);
      }
    });
  }

  function sendMsg(action, payload) {
    return new Promise((resolve) => {
      if (!wsReady) {
        warn("WS not ready, action:", action);
        resolve(null);
        return;
      }
      log("Send:", action, payload || "");
      pendingCallbacks[action] = resolve;
      ws.send(JSON.stringify({ action, ...payload }));
      setTimeout(() => {
        if (pendingCallbacks[action]) {
          delete pendingCallbacks[action];
          resolve(null);
        }
      }, 5000);
    });
  }

  // Fire-and-forget (for video_play/pause/seek - no response expected)
  function sendFire(action, payload) {
    if (!wsReady) return;
    try {
      ws.send(JSON.stringify({ action, ...(payload || {}) }));
    } catch (e) {
      warn("sendFire failed", e);
    }
  }

  // ---------- Apply status ----------
    function applyStatus(s) {
      const txt = document.querySelector("#ab-toolbar .ab-status-text");
      if (txt && txt.dataset.custom === "1") return;

      const bar = document.getElementById("ab-toolbar");
      if (!bar) return;

      log("applyStatus, enabled=", s.enabled, "armed=", s.armed, "playing=", s.video_playing);

      // LED is only green when everything is active
      const effective = s.enabled && s.armed && s.video_playing;
      setLed(s.enabled ? (effective ? "ok" : "idle") : "idle");

      updateScripts(s.scripts, s.current);

      // Play/Pause button reflects ARMED state
      if (typeof s.armed === "boolean") {
          bar.dataset.armed = s.armed ? "true" : "false";
          const btn = bar.querySelector("[data-action='playpause']");
          if (btn) {
              btn.textContent = s.armed ? "⏸" : "▶";
              btn.title = s.armed ? "Pause script" : "Resume script";
          }
      }

      // Master switch
      const sw = bar.querySelector(".ab-enable");
      if (sw && typeof s.enabled === "boolean") {
          sw.checked = s.enabled;
      }

      // Derive status text (skip if a custom text is currently locked)
      if (txt && !txt.dataset.custom) {
          let label;
          if (!s.enabled) label = "script disabled";
          else if (!s.armed) label = "script paused";
          else if (!s.video_playing) label = "script paused";
          else label = "script playing";
          txt.textContent = label;
      }

      if (s.message) showInfo(s.message);
  }

  // ---------- Info display ----------
  function showInfo(msg, duration = 3000) {
    // Persistent status text inside the toolbar
    const txt = document.querySelector("#ab-toolbar .ab-status-text");
    if (txt) { txt.textContent = msg; txt.dataset.custom = "1"; }
    const isUploading = /upload/i.test(msg) && !/finished|ready/i.test(msg);
    clearTimeout(txt && txt._customTimer);
    if (txt) {
      if (isUploading) {
        // Keep lock until next message arrives
      } else {
        txt._customTimer = setTimeout(() => { delete txt.dataset.custom; }, duration);
      }
    }

    // Toast notification
    let info = document.getElementById("ab-info");
    if (!info) {
      info = document.createElement("div");
      info.id = "ab-info";
      info.style.cssText = `
        position: fixed; bottom: 60px; right: 20px;
        background: rgba(0,0,0,0.85); color: #fff;
        padding: 8px 16px; border-radius: 8px;
        font-size: 13px; z-index: 99999;
        transition: opacity 0.4s; pointer-events: none;
      `;
      document.body.appendChild(info);
    }
    info.textContent = msg;
    info.style.opacity = "1";
    clearTimeout(info._timeout);
    info._timeout = setTimeout(() => { info.style.opacity = "0"; }, duration);
  }


  // ---------- Build toolbar ----------
  function buildToolbar() {
    if (document.getElementById("ab-toolbar")) return;

    const bar = document.createElement("div");
    bar.id = "ab-toolbar";
    bar.className = "ab-toolbar";
    bar.innerHTML = `
      <span class="ab-handle" title="Drag to move">≡</span>
      <button class="ab-btn" data-action="playpause" title="Play / Pause script">⏯</button>
      <label class="ab-switch" title="Enable / Disable script">
        <input type="checkbox" class="ab-enable" checked>
        <span class="ab-slider"></span>
      </label>
      <button class="ab-btn" data-action="offset" title="Set offset (ms)">⏱</button>
      <button class="ab-btn" data-action="reupload" title="Re-upload funscript">⟳</button>
      <select class="ab-select" title="Select funscript variant"></select>
      <div class="ab-status" title="Status">
        <span class="ab-led"></span>
        <span class="ab-status-text">idle</span>
      </div>
      <button class="ab-btn ab-min" title="Minimize">–</button>
    `;

    document.body.appendChild(bar);

    // Play/Pause = arm/disarm
    bar.querySelector("[data-action='playpause']").addEventListener("click", () => {
      const armed = bar.dataset.armed === "true";
      const action = armed ? "disarm" : "arm";
      const video = document.querySelector("video");
      const time_ms = video ? Math.floor(video.currentTime * 1000) : 0;
      log("Toolbar:", action);

      // Optimistic UI: update button + text immediately
      bar.dataset.armed = armed ? "false" : "true";
      const btn = bar.querySelector("[data-action='playpause']");
      if (btn) btn.textContent = armed ? "▶" : "⏸";
      const txt = document.querySelector("#ab-toolbar .ab-status-text");
      if (txt) {
        if (armed) txt.textContent = "script paused";
        else txt.textContent = (video && !video.paused) ? "script playing" : "script paused";
        txt.dataset.custom = "1";
        setTimeout(() => { delete txt.dataset.custom; }, 3000);
      }

      // Don't blindly apply server response — optimistic UI is authoritative here
      sendMsg(action, { time_ms });
    });


    // Master enable/disable
    bar.querySelector(".ab-enable").addEventListener("change", e => {
      const action = e.target.checked ? "enable" : "disable";
      const video = document.querySelector("video");
      const time_ms = video ? Math.floor(video.currentTime * 1000) : 0;
      const is_playing = video ? !video.paused : false;
      log("Master:", action);

      // Optimistic UI
      const txt = document.querySelector("#ab-toolbar .ab-status-text");
      if (txt) txt.textContent = e.target.checked ? "script ready" : "script disabled";

      sendMsg(action, { time_ms, is_playing }).then(r => { if (r) applyStatus(r); });
    });

    // Reupload — backend pushes status updates via info()
    bar.querySelector("[data-action='reupload']").addEventListener("click", () => {
      const video = document.querySelector("video");
      const time_ms = video ? Math.floor(video.currentTime * 1000) : 0;
      const is_playing = video ? !video.paused : false;
      // Lock status text until script_ready arrives
      const txt = document.querySelector("#ab-toolbar .ab-status-text");
      if (txt) { txt.textContent = "uploading script"; txt.dataset.custom = "1"; }
      sendMsg("reupload", { time_ms, is_playing });
    });

    // Script selection
    bar.querySelector(".ab-select").addEventListener("change", e => {
      const video = document.querySelector("video");
      const time_ms = video ? Math.floor(video.currentTime * 1000) : 0;
      const is_playing = video ? !video.paused : false;
      const txt = document.querySelector("#ab-toolbar .ab-status-text");
      if (txt) { txt.textContent = "uploading script"; txt.dataset.custom = "1"; }
      sendMsg("select", { script: e.target.value, time_ms, is_playing });
    });



    // Minimize
    bar.querySelector(".ab-min").addEventListener("click", () => {
      bar.classList.toggle("ab-collapsed");
    });

    // ---------- Offset popup ----------
    const popup = document.createElement("div");
    popup.id = "ab-offset-popup";
    popup.style.cssText = `
      position: fixed; display: none; z-index: 99998;
      background: rgba(20,20,20,0.95); color: #fff;
      padding: 12px 14px; border-radius: 8px;
      font-size: 13px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);
      border: 1px solid #444;
    `;
    popup.innerHTML = `
        <div style="font-weight:bold; margin-bottom:8px; text-align:center;">
          Offset (ms)
          <span title="If device reacts too late → increase.&#10;If device reacts too early → decrease.&#10;Range: -2000 to 2000"
                style="cursor:help; opacity:0.6; margin-left:4px;">ⓘ</span>
        </div>
      <div style="display:flex; align-items:center; gap:6px;">
        <button class="ab-off-dec" style="width:28px;height:28px;cursor:pointer;">−</button>
        <input type="number" class="ab-off-input" min="-2000" max="2000" step="1" value="0"
          style="width:80px;text-align:center;background:#222;color:#fff;border:1px solid #555;border-radius:4px;padding:4px;">
        <button class="ab-off-inc" style="width:28px;height:28px;cursor:pointer;">+</button>
      </div>
      <div style="margin-top:8px; text-align:center;">
        <button class="ab-off-apply" style="cursor:pointer;padding:4px 12px;">Apply</button>
        <button class="ab-off-close" style="cursor:pointer;padding:4px 12px;margin-left:4px;">Close</button>
      </div>
    `;
    document.body.appendChild(popup);

    const offBtn   = bar.querySelector("[data-action='offset']");
    const offInput = popup.querySelector(".ab-off-input");
    const STEP = 50;

    function clampOffset(v) {
      v = parseInt(v, 10) || 0;
      return Math.max(-2000, Math.min(2000, v));
    }
    function sendOffset() {
      const v = clampOffset(offInput.value);
      offInput.value = v;
      sendMsg("set_offset", { offset_ms: v });
    }

    offBtn.addEventListener("click", () => {
      if (popup.style.display === "block") { popup.style.display = "none"; return; }
      // Fetch current value from backend
      sendMsg("status").then(s => {
        if (s && typeof s.offset_ms === "number") offInput.value = s.offset_ms;
      });
      const r = offBtn.getBoundingClientRect();
      popup.style.left = r.left + "px";
      popup.style.top  = "auto";
      popup.style.bottom = (window.innerHeight - r.top + 6) + "px";
      popup.style.display = "block";
    });

    popup.querySelector(".ab-off-dec").addEventListener("click", () => {
      offInput.value = clampOffset(parseInt(offInput.value, 10) - STEP);
    });
    popup.querySelector(".ab-off-inc").addEventListener("click", () => {
      offInput.value = clampOffset(parseInt(offInput.value, 10) + STEP);
    });
    popup.querySelector(".ab-off-apply").addEventListener("click", sendOffset);
    popup.querySelector(".ab-off-close").addEventListener("click", () => {
      popup.style.display = "none";
    });
    offInput.addEventListener("keydown", e => {
      if (e.key === "Enter") { sendOffset(); }
    });

    // Click outside closes the popup
    document.addEventListener("mousedown", e => {
      if (popup.style.display !== "block") return;
      if (popup.contains(e.target) || offBtn.contains(e.target)) return;
      popup.style.display = "none";
    });


    makeDraggable(bar, bar.querySelector(".ab-handle"));
    restorePos(bar);
  }

  // ---------- Scene detection ----------
  function getSceneIdFromUrl() {
    const m = location.href.match(/\/scenes\/(\d+)/);
    return m ? m[1] : null;
  }

  function checkScene() {
    const id = getSceneIdFromUrl();
    if (id && id !== lastSceneId) {
      lastSceneId = id;
      sendMsg("scene", { scene_id: id }).then(data => {
        if (data) {
          updateScripts(data.scripts, data.current);
          applyStatus(data);
        }
      });
      // Re-attach video listeners after a scene change
      setTimeout(attachVideoListeners, 500);
    } else if (!id && lastSceneId) {
      lastSceneId = null;
    }
  }

  // ---------- Video events ----------
  function attachVideoListeners() {
    const video = document.querySelector("video");
    if (!video) return;
    if (video.dataset.abAttached === "1") return;
    video.dataset.abAttached = "1";
    log("Video listeners attached");

    function setStatusText(label) {
      const txt = document.querySelector("#ab-toolbar .ab-status-text");
      if (txt) txt.textContent = label;
    }

    video.addEventListener("play", () => {
      const t = Math.floor(video.currentTime * 1000);
      log("video play @", t);
      sendFire("video_play", { time_ms: t });
      setStatusText("script playing");
    });

    video.addEventListener("pause", () => {
      if (video.seeking) return;
      log("video pause");
      sendFire("video_pause");
      setStatusText("script paused");
    });

    video.addEventListener("seeked", () => {
      const now = Date.now();
      if (now - lastSeekSent < SEEK_DEBOUNCE_MS) return;
      lastSeekSent = now;
      const t = Math.floor(video.currentTime * 1000);
      log("video seek @", t);
      sendFire("video_seek", { time_ms: t, playing: !video.paused });
    });

    video.addEventListener("ended", () => {
      log("video ended");
      sendFire("video_pause");
    });
  }

  // ---------- Drag & drop ----------
  function makeDraggable(el, handle) {
    let dragging = false, offX = 0, offY = 0;
    const start = (x, y) => {
      dragging = true;
      const r = el.getBoundingClientRect();
      offX = x - r.left; offY = y - r.top;
      el.classList.add("ab-dragging");
    };
    const move = (x, y) => {
      if (!dragging) return;
      const nx = Math.max(0, Math.min(window.innerWidth  - el.offsetWidth,  x - offX));
      const ny = Math.max(0, Math.min(window.innerHeight - el.offsetHeight, y - offY));
      el.style.left = nx + "px";
      el.style.top  = ny + "px";
      el.style.right = "auto"; el.style.bottom = "auto";
    };
    const end = () => {
      if (!dragging) return;
      dragging = false;
      el.classList.remove("ab-dragging");
      localStorage.setItem("ab-pos", JSON.stringify({ left: el.style.left, top: el.style.top }));
    };

    handle.addEventListener("mousedown", e => { e.preventDefault(); start(e.clientX, e.clientY); });
    window.addEventListener("mousemove", e => move(e.clientX, e.clientY));
    window.addEventListener("mouseup", end);

    handle.addEventListener("touchstart", e => {
      const t = e.touches[0]; start(t.clientX, t.clientY);
    }, { passive: true });
    window.addEventListener("touchmove", e => {
      if (!dragging) return;
      const t = e.touches[0]; move(t.clientX, t.clientY);
    }, { passive: true });
    window.addEventListener("touchend", end);
  }

  function restorePos(el) {
    if (window.innerWidth < 768) return;
    try {
      const p = JSON.parse(localStorage.getItem("ab-pos") || "{}");
      if (p.left) { el.style.left = p.left; el.style.right = "auto"; }
      if (p.top)  { el.style.top  = p.top;  el.style.bottom = "auto"; }
    } catch {}
  }

  // ---------- UI helpers ----------
  function setLed(state) {
    const led = document.querySelector("#ab-toolbar .ab-led");
    if (!led) return;
    led.dataset.state = state || "idle";
    // Status text is handled by applyStatus
  }

  function updateScripts(list, current) {
    const sel = document.querySelector("#ab-toolbar .ab-select");
    if (!sel || !Array.isArray(list)) return;
    const same = sel.options.length === list.length &&
      Array.from(sel.options).every((o, i) => o.value === list[i]);
    if (!same) {
      sel.innerHTML = list.map(s =>
        `<option value="${s}">${s.split("/").pop()}</option>`
      ).join("");
    }
    if (current && sel.value !== current) sel.value = current;
  }

  // ---------- Polling ----------
  function startUrlWatcher() {
    let lastHref = location.href;
    setInterval(() => {
      if (location.href !== lastHref) {
        lastHref = location.href;
        setTimeout(checkScene, 500);
      }
      // Video element may appear without a URL change (lazy mount)
      attachVideoListeners();
    }, 500);
  }

  function startStatusPoll() {
    setInterval(() => {
      if (!wsReady) return;
      sendMsg("status").then(s => applyStatus(s));
    }, 3000);
  }

  // ---------- Backend trigger ----------
  function triggerBackend() {
    fetch('/graphql', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        query: `mutation { runPluginTask(plugin_id: "autoblow_sync", task_name: "Start Backend") }`
      })
    }).catch(() => {});
  }

  function adjustForStashNav() {
    const bar = document.getElementById("ab-toolbar");
    if (!bar) return;
    const nav = document.querySelector("nav.top-nav");
    if (!nav) { bar.style.bottom = ""; return; }

    const barAtBottom = window.innerWidth < 768;
    if (!barAtBottom) { bar.style.bottom = ""; return; }

    const navRect = nav.getBoundingClientRect();
    const navIsAtBottom = navRect.top > window.innerHeight / 2;

    if (navIsAtBottom) {
      bar.style.bottom = nav.offsetHeight + "px";
    } else {
      bar.style.bottom = "";
    }
  }

  // ---------- Init ----------
  function init() {
    buildToolbar();
    const txt0 = document.querySelector("#ab-toolbar .ab-status-text");
    if (txt0) { txt0.textContent = "starting backend"; txt0.dataset.custom = "1"; }
    triggerBackend();
    setTimeout(connectWS, 500);
    startUrlWatcher();
    startStatusPoll();
    adjustForStashNav();
    setTimeout(adjustForStashNav, 1000);
    window.addEventListener("resize", adjustForStashNav);

    const sendPauseOnExit = () => {
      try {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: "video_pause", time_ms: 0 }));
        }
      } catch (e) {}
    };
    window.addEventListener("pagehide", sendPauseOnExit);
    window.addEventListener("beforeunload", sendPauseOnExit);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") sendPauseOnExit();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
