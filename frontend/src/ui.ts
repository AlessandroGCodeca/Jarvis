/**
 * DOM/UI layer for JARVIS.
 *
 * Builds the mic button, status indicator, transcript line, conversation log
 * (toggle with the "L" key), and a settings panel that stores API keys in
 * localStorage. All elements are created dynamically and appended to <body>.
 */

type LogRole = "user" | "jarvis";

export class UI {
  private statusEl!: HTMLDivElement;
  private transcriptEl!: HTMLDivElement;
  private micBtn!: HTMLButtonElement;
  private logPanel!: HTMLDivElement;
  private settingsPanel!: HTMLDivElement;

  private logEntries: { role: LogRole; text: string }[] = [];

  // Callbacks wired by main.ts.
  private micHandler: () => void = () => {};

  constructor() {
    this.buildStatus();
    this.buildSettingsButton();
    this.buildLogButton();
    this.buildTranscript();
    this.buildMicButton();
    this.buildLogPanel();
    this.buildSettingsPanel();
    this.bindKeys();
  }

  // ---- Builders ----
  private buildStatus(): void {
    this.statusEl = document.createElement("div");
    this.statusEl.id = "status";
    this.statusEl.className = "disconnected";
    this.statusEl.innerHTML = `<span class="dot"></span><span class="label">Disconnected</span>`;
    document.body.appendChild(this.statusEl);
  }

  private buildSettingsButton(): void {
    const btn = document.createElement("button");
    btn.id = "settings-btn";
    btn.textContent = "Settings";
    btn.addEventListener("click", () => this.toggleSettings());
    document.body.appendChild(btn);
  }

  private buildLogButton(): void {
    const btn = document.createElement("button");
    btn.id = "log-btn";
    btn.textContent = "Log (L)";
    btn.addEventListener("click", () => this.toggleLog());
    document.body.appendChild(btn);
  }

  private buildTranscript(): void {
    this.transcriptEl = document.createElement("div");
    this.transcriptEl.id = "transcript";
    document.body.appendChild(this.transcriptEl);
  }

  private buildMicButton(): void {
    this.micBtn = document.createElement("button");
    this.micBtn.id = "mic-btn";
    this.micBtn.setAttribute("aria-label", "Toggle microphone");
    this.micBtn.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
        <line x1="12" y1="19" x2="12" y2="23"></line>
        <line x1="8" y1="23" x2="16" y2="23"></line>
      </svg>`;
    this.micBtn.addEventListener("click", () => this.micHandler());
    document.body.appendChild(this.micBtn);
  }

  private buildLogPanel(): void {
    this.logPanel = document.createElement("div");
    this.logPanel.id = "log-panel";
    document.body.appendChild(this.logPanel);
  }

  private buildSettingsPanel(): void {
    this.settingsPanel = document.createElement("div");
    this.settingsPanel.id = "settings-panel";
    this.settingsPanel.innerHTML = `
      <h3>API Keys</h3>
      <label for="set-anthropic">Anthropic API Key</label>
      <input id="set-anthropic" type="password" placeholder="sk-ant-..." />
      <label for="set-eleven">ElevenLabs API Key</label>
      <input id="set-eleven" type="password" placeholder="..." />
      <label for="set-voice">ElevenLabs Voice ID</label>
      <input id="set-voice" type="text" placeholder="voice id" />
      <button id="set-save">Save</button>
      <p class="note">Stored locally in your browser. The backend reads its
      keys from <code>backend/.env</code> — these are for reference/convenience.</p>
    `;
    document.body.appendChild(this.settingsPanel);

    // Hydrate from localStorage.
    (this.settingsPanel.querySelector("#set-anthropic") as HTMLInputElement).value =
      localStorage.getItem("jarvis.anthropicKey") || "";
    (this.settingsPanel.querySelector("#set-eleven") as HTMLInputElement).value =
      localStorage.getItem("jarvis.elevenKey") || "";
    (this.settingsPanel.querySelector("#set-voice") as HTMLInputElement).value =
      localStorage.getItem("jarvis.voiceId") || "";

    (this.settingsPanel.querySelector("#set-save") as HTMLButtonElement).addEventListener(
      "click",
      () => {
        localStorage.setItem(
          "jarvis.anthropicKey",
          (this.settingsPanel.querySelector("#set-anthropic") as HTMLInputElement).value
        );
        localStorage.setItem(
          "jarvis.elevenKey",
          (this.settingsPanel.querySelector("#set-eleven") as HTMLInputElement).value
        );
        localStorage.setItem(
          "jarvis.voiceId",
          (this.settingsPanel.querySelector("#set-voice") as HTMLInputElement).value
        );
        this.toggleSettings();
      }
    );
  }

  private bindKeys(): void {
    window.addEventListener("keydown", (e) => {
      // Ignore when typing in the settings inputs.
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "l" || e.key === "L") {
        this.toggleLog();
      }
    });
  }

  // ---- Public API ----
  onMicToggle(handler: () => void): void {
    this.micHandler = handler;
  }

  setMicActive(active: boolean): void {
    this.micBtn.classList.toggle("listening", active);
  }

  setStatus(state: "connected" | "disconnected"): void {
    this.statusEl.className = state;
    const label = this.statusEl.querySelector(".label");
    if (label) {
      label.textContent = state === "connected" ? "Connected" : "Disconnected";
    }
  }

  showTranscript(text: string, interim = false): void {
    this.transcriptEl.textContent = text;
    this.transcriptEl.classList.toggle("interim", interim);
  }

  clearTranscript(): void {
    this.transcriptEl.textContent = "";
    this.transcriptEl.classList.remove("interim");
  }

  addToLog(role: LogRole, text: string): void {
    this.logEntries.push({ role, text });
    // Keep the last 10 exchanges (20 entries).
    if (this.logEntries.length > 20) {
      this.logEntries = this.logEntries.slice(-20);
    }
    this.renderLog();
  }

  private renderLog(): void {
    this.logPanel.innerHTML = this.logEntries
      .map((entry) => {
        const roleLabel = entry.role === "user" ? "You" : "JARVIS";
        return `<div class="entry ${entry.role}"><span class="role">${roleLabel}</span>${escapeHtml(
          entry.text
        )}</div>`;
      })
      .join("");
    this.logPanel.scrollTop = this.logPanel.scrollHeight;
  }

  private toggleLog(): void {
    this.logPanel.classList.toggle("open");
  }

  showSettings(): void {
    this.settingsPanel.classList.add("open");
  }

  private toggleSettings(): void {
    this.settingsPanel.classList.toggle("open");
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
