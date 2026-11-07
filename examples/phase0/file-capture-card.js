// Phase 0 test card (dev instance only): does a file input with
// capture="environment" offer the camera inside the Home Assistant app?
// Copy to config/www/qm-phase0/ and add /local/qm-phase0/file-capture-card.js
// as a dashboard resource of type module, then add a "custom:qm-file-capture-test" card.
class QmFileCaptureTest extends HTMLElement {
  setConfig() {}

  set hass(_hass) {
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <ha-card header="Phase 0 file capture test">
        <div style="padding: 16px; display: grid; gap: 16px;">
          <label>A: accept="image/*" capture="environment"<br>
            <input id="a" type="file" accept="image/*" capture="environment"></label>
          <label>B: accept="image/*" (no capture)<br>
            <input id="b" type="file" accept="image/*"></label>
          <pre id="out" style="white-space: pre-wrap; margin: 0;">${navigator.userAgent}</pre>
        </div>
      </ha-card>`;
    const out = root.getElementById("out");
    for (const id of ["a", "b"]) {
      root.getElementById(id).addEventListener("change", (ev) => {
        const file = ev.target.files[0];
        out.textContent = file
          ? `${id}: ${file.type} ${Math.round(file.size / 1024)} KiB, ` +
            `lastModified ${new Date(file.lastModified).toISOString()}`
          : `${id}: nothing selected`;
      });
    }
  }

  getCardSize() {
    return 3;
  }
}

customElements.define("qm-file-capture-test", QmFileCaptureTest);
