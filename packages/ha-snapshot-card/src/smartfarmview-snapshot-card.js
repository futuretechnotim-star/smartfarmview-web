class SmartFarmViewSnapshotCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._rendered = false;
    this._intervalId = null;
  }

  static getStubConfig() {
    return {
      camera_entity: "camera.landplanmesh1_camera",
      button_entity: "button.landplanmesh1_capture_snapshot",
    };
  }

  setConfig(config) {
    if (!config.camera_entity) throw new Error("camera_entity is required");
    if (!config.button_entity) throw new Error("button_entity is required");
    this._config = config;
    if (!this._rendered) {
      this._render();
      this._rendered = true;
    }
  }

  set hass(hass) {
    this._hass = hass;
    this._updateImage();
    this._updateAge();
  }

  connectedCallback() {
    this._intervalId = setInterval(() => this._updateAge(), 30_000);
  }

  disconnectedCallback() {
    clearInterval(this._intervalId);
    this._intervalId = null;
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }
        .image-wrap {
          position: relative;
          background: var(--secondary-background-color, #111);
          min-height: 120px;
        }
        img {
          width: 100%;
          display: block;
          object-fit: contain;
        }
        .placeholder {
          display: flex;
          align-items: center;
          justify-content: center;
          min-height: 200px;
          color: var(--secondary-text-color);
          font-size: 0.88em;
          font-family: var(--primary-font-family, sans-serif);
        }
        .footer {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 10px 14px;
          border-top: 1px solid var(--divider-color, rgba(0,0,0,0.12));
        }
        .age {
          font-size: 0.82em;
          color: var(--secondary-text-color);
          font-family: var(--primary-font-family, sans-serif);
        }
        .capture-btn {
          background: var(--primary-color);
          color: var(--text-primary-color, #fff);
          border: none;
          border-radius: 6px;
          padding: 6px 18px;
          font-size: 0.88em;
          font-family: var(--primary-font-family, sans-serif);
          cursor: pointer;
          transition: opacity 0.15s;
        }
        .capture-btn:hover:not(:disabled) { opacity: 0.85; }
        .capture-btn:disabled { opacity: 0.5; cursor: default; }
      </style>
      <ha-card header="${this._config.title || ""}">
        <div class="image-wrap" id="image-wrap">
          <div class="placeholder" id="placeholder">No snapshot yet</div>
        </div>
        <div class="footer">
          <span class="age" id="age">–</span>
          <button class="capture-btn" id="capture-btn">Capture</button>
        </div>
      </ha-card>
    `;
    this.shadowRoot
      .getElementById("capture-btn")
      .addEventListener("click", () => this._capture());
  }

  _cameraEntity() {
    return this._hass?.states[this._config?.camera_entity];
  }

  _imageUrl() {
    const entity = this._cameraEntity();
    const token = entity?.attributes?.access_token;
    if (!token) return null;
    return `/api/camera_proxy/${this._config.camera_entity}?token=${token}`;
  }

  _updateImage() {
    if (!this._rendered) return;
    const url = this._imageUrl();
    const wrap = this.shadowRoot.getElementById("image-wrap");
    const placeholder = this.shadowRoot.getElementById("placeholder");
    if (!url) {
      placeholder.style.display = "flex";
      wrap.querySelector("img")?.remove();
      return;
    }
    // Cache-bust using entity last_updated so the browser refetches on new captures
    const entity = this._cameraEntity();
    const bust = entity?.last_updated
      ? new Date(entity.last_updated).getTime()
      : Date.now();
    const src = `${url}&_t=${bust}`;

    let img = wrap.querySelector("img");
    if (!img) {
      placeholder.style.display = "none";
      img = document.createElement("img");
      img.alt = "Field node snapshot";
      wrap.appendChild(img);
    }
    if (img.dataset.src !== src) {
      img.dataset.src = src;
      img.src = src;
    }
  }

  _updateAge() {
    if (!this._rendered) return;
    const el = this.shadowRoot.getElementById("age");
    if (!el) return;
    const entity = this._cameraEntity();
    if (!entity) {
      el.textContent = "–";
      return;
    }
    const ageMin = Math.round(
      (Date.now() - new Date(entity.last_updated).getTime()) / 60_000
    );
    if (ageMin < 1) el.textContent = "captured just now";
    else if (ageMin === 1) el.textContent = "captured 1 minute ago";
    else el.textContent = `captured ${ageMin} minutes ago`;
  }

  async _capture() {
    if (!this._hass || !this._config) return;
    const btn = this.shadowRoot.getElementById("capture-btn");
    btn.disabled = true;
    btn.textContent = "Capturing…";
    try {
      await this._hass.callService("button", "press", {
        entity_id: this._config.button_entity,
      });
    } finally {
      // Re-enable after 5s — the Pi needs time to capture and publish
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = "Capture";
      }, 5_000);
    }
  }

  getCardSize() {
    return 4;
  }
}

customElements.define(
  "smartfarmview-snapshot-card",
  SmartFarmViewSnapshotCard
);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "smartfarmview-snapshot-card",
  name: "SmartFarmView Snapshot Card",
  description:
    "Latest snapshot from a SecurityMesh field node with timestamp and on-demand capture.",
  preview: false,
});
