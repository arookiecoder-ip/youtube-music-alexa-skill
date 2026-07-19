(function () {
    "use strict";

    const panel = document.createElement("div");
    panel.id = "ytmusic-auth-controls";
    panel.innerHTML = `
      <div class="yt-title">YouTube Music login</div>
      <button type="button" data-action="open">Open YouTube Music</button>
      <button type="button" data-action="capture" class="primary">Capture signed-in session</button>
      <div class="yt-status" role="status">Sign in first, then capture.</div>`;

    const overlay = document.createElement("div");
    overlay.id = "ytmusic-capture-overlay";
    overlay.setAttribute("role", "status");
    overlay.setAttribute("aria-live", "assertive");
    overlay.innerHTML = `<div class="capture-card"><div class="capture-spinner"></div>
      <div class="capture-title">Saving your session</div>
      <div class="capture-message">Validating your YouTube Music sign-in...</div></div>`;

    const style = document.createElement("style");
    style.textContent = `
      #ytmusic-auth-controls{position:fixed;right:16px;bottom:16px;z-index:10000;
        width:250px;padding:12px;background:rgba(24,24,24,.94);color:#eee;
        border:1px solid #555;border-radius:8px;font:14px Arial,sans-serif;
        box-shadow:0 5px 22px rgba(0,0,0,.45)}
      #ytmusic-auth-controls .yt-title{font-weight:700;margin:0 0 9px}
      #ytmusic-auth-controls button{display:block;width:100%;margin:7px 0;padding:9px;
        color:#eee;background:#343434;border:1px solid #777;border-radius:5px;cursor:pointer}
      #ytmusic-auth-controls button.primary{background:#e95400;border-color:#ff731f;color:#fff}
      #ytmusic-auth-controls button:disabled{opacity:.55;cursor:wait}
      #ytmusic-auth-controls .yt-status{min-height:17px;margin-top:8px;color:#ccc;font-size:12px}
      #ytmusic-capture-overlay{position:fixed;inset:0;z-index:2147483647;display:flex;
        align-items:center;justify-content:center;background:rgba(5,7,11,.84);backdrop-filter:blur(5px);
        -webkit-backdrop-filter:blur(5px);opacity:0;visibility:hidden;pointer-events:none;
        transition:opacity .35s ease,visibility 0s linear .35s}
      #ytmusic-capture-overlay.visible{opacity:1;visibility:visible;pointer-events:auto;transition:opacity .22s ease}
      #ytmusic-capture-overlay.fade-out{opacity:0;pointer-events:none;transition:opacity .7s ease}
      #ytmusic-capture-overlay .capture-card{width:min(320px,calc(100vw - 48px));padding:28px 24px;
        box-sizing:border-box;text-align:center;color:#f7f7f7;font:14px Arial,sans-serif;
        background:rgba(29,31,36,.98);border:1px solid rgba(255,255,255,.18);border-radius:12px;
        box-shadow:0 20px 55px rgba(0,0,0,.55)}
      #ytmusic-capture-overlay .capture-spinner{width:34px;height:34px;margin:0 auto 16px;
        border:3px solid rgba(255,255,255,.2);border-top-color:#ff6500;border-radius:50%;animation:ytCaptureSpin .8s linear infinite}
      #ytmusic-capture-overlay .capture-title{font-weight:700;font-size:16px;margin-bottom:8px}
      #ytmusic-capture-overlay .capture-message{color:#c9c9c9;line-height:1.45}
      /* Draw the success tick with borders instead of a Unicode glyph: the
         minimal font set in the VNC browser does not always render ✓ cleanly. */
      #ytmusic-capture-overlay.success .capture-spinner{width:15px;height:28px;margin:0 auto 16px;
        border:solid #49c77a;border-width:0 4px 4px 0;border-radius:0;animation:none;transform:rotate(45deg)}
      #ytmusic-capture-overlay.failure .capture-spinner{border-color:#ff7777;animation:none;position:relative}
      #ytmusic-capture-overlay.failure .capture-spinner::after{content:'!';position:absolute;inset:-2px;display:grid;place-items:center;color:#ff7777;font-size:24px;font-weight:700}
      @keyframes ytCaptureSpin{to{transform:rotate(360deg)}}`;

    let completionPoll = null;
    let overlayTimer = null;

    function setButtonsDisabled(disabled) {
        panel.querySelectorAll("button").forEach((button) => { button.disabled = disabled; });
    }

    function stopCompletionPoll() {
        if (completionPoll) window.clearTimeout(completionPoll);
        completionPoll = null;
    }

    function showCaptureOverlay(title, message, state) {
        if (overlayTimer) window.clearTimeout(overlayTimer);
        overlayTimer = null;
        overlay.className = state || "";
        overlay.querySelector(".capture-title").textContent = title;
        overlay.querySelector(".capture-message").textContent = message;
        overlay.classList.add("visible");
    }

    function failCapture(message) {
        stopCompletionPoll();
        showCaptureOverlay("Could not save the session", message || "Please try again.", "failure");
        // Leave the reason visible, then restore the VNC page gradually.
        overlayTimer = window.setTimeout(() => {
            overlay.classList.add("fade-out");
            overlayTimer = window.setTimeout(() => {
                overlay.className = "";
                setButtonsDisabled(false);
            }, 700);
        }, 1500);
    }

    // Close only after Flask has validated and promoted the captured headers.
    // Capture failures and Google sign-in challenges deliberately leave the
    // VNC page open so the owner can retry without starting a new session.
    async function waitForSuccessfulCapture() {
        stopCompletionPoll();
        try {
            const response = await fetch("/api/youtube/browser-session/status", {
                credentials: "same-origin",
                headers: { "Accept": "application/json" }
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.error || "Could not check session status");
            if (body.state === "connected") {
                panel.querySelector(".yt-status").textContent = "Connected. Closing this tab...";
                showCaptureOverlay("Session saved", "Your personalized YouTube Music data is ready.", "success");
                window.setTimeout(() => window.close(), 700);
                return;
            }
            // After an explicit Capture request, waiting_for_login means the
            // browser validation found no signed-in YT Music account. It is a
            // retryable failure, not an in-progress state; otherwise the
            // blocking overlay would spin forever for a signed-out browser.
            if (["waiting_for_login", "reconnect_required", "unavailable", "idle"].includes(body.state)) {
                const message = body.message || (body.state === "waiting_for_login"
                    ? "YouTube Music is still signed out. Sign in, then capture again."
                    : "Session was not saved. Try again.");
                panel.querySelector(".yt-status").textContent = message;
                failCapture(message);
                return;
            }
            completionPoll = window.setTimeout(waitForSuccessfulCapture, 1000);
        } catch (error) {
            const message = error.message || "Could not check session status";
            panel.querySelector(".yt-status").textContent = message;
            failCapture(message);
        }
    }

    async function invoke(action) {
        const status = panel.querySelector(".yt-status");
        setButtonsDisabled(true);
        status.textContent = action === "capture" ? "Saving and validating session..." : "Opening YouTube Music...";
        if (action === "capture") {
            showCaptureOverlay("Saving your session", "Validating your YouTube Music sign-in...");
        }
        try {
            const response = await fetch(`/api/youtube/browser-session/${action === "open" ? "open-youtube" : "capture"}`, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                },
                body: "{}"
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.error || "Request failed");
            status.textContent = action === "capture"
                ? "Capture requested. Keep this tab open while it validates."
                : "YouTube Music opened. Sign in, then capture.";
            if (action === "capture") waitForSuccessfulCapture();
        } catch (error) {
            const message = error.message || "Request failed";
            status.textContent = message;
            if (action === "capture") failCapture(message);
        } finally {
            // Capture remains blocked until its validation finishes. Opening
            // YouTube Music is still immediately reusable.
            if (action !== "capture") setButtonsDisabled(false);
        }
    }

    panel.querySelector('[data-action="open"]').addEventListener("click", () => invoke("open"));
    panel.querySelector('[data-action="capture"]').addEventListener("click", () => invoke("capture"));
    window.addEventListener("pagehide", stopCompletionPoll);
    document.head.appendChild(style);
    document.body.appendChild(panel);
    document.body.appendChild(overlay);
}());
