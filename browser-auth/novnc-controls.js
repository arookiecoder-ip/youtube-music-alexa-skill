(function () {
    "use strict";

    const panel = document.createElement("div");
    panel.id = "ytmusic-auth-controls";
    panel.innerHTML = `
      <div class="yt-title">YouTube Music login</div>
      <button type="button" data-action="open">Open YouTube Music</button>
      <button type="button" data-action="capture" class="primary">Capture signed-in session</button>
      <div class="yt-status" role="status">Sign in first, then capture.</div>`;

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
      #ytmusic-auth-controls .yt-status{min-height:17px;margin-top:8px;color:#ccc;font-size:12px}`;

    async function invoke(action) {
        const buttons = panel.querySelectorAll("button");
        const status = panel.querySelector(".yt-status");
        buttons.forEach((button) => { button.disabled = true; });
        status.textContent = action === "capture" ? "Saving and validating session…" : "Opening YouTube Music…";
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
        } catch (error) {
            status.textContent = error.message || "Request failed";
        } finally {
            buttons.forEach((button) => { button.disabled = false; });
        }
    }

    panel.querySelector('[data-action="open"]').addEventListener("click", () => invoke("open"));
    panel.querySelector('[data-action="capture"]').addEventListener("click", () => invoke("capture"));
    document.head.appendChild(style);
    document.body.appendChild(panel);
}());
