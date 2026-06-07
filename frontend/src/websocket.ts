import type { OrbVisualizer } from "./orb";

interface ResponseMessage {
  type: "response";
  text: string;
}

/**
 * WebSocket client for the JARVIS backend.
 *
 * Sends `{type:"message", content}` and receives `{type:"response", text,
 * audio}`. Decodes base64 audio into the orb's shared AudioContext and plays it
 * (which also drives the speaking animation). Auto-reconnects with exponential
 * backoff, and drives orb state across the message flow.
 */
export class WSClient {
  private url: string;
  private ws: WebSocket | null = null;
  private connected = false;
  private reconnectAttempts = 0;
  private readonly maxReconnect = 5;
  private pingTimer: number | null = null;

  private orb: OrbVisualizer | null = null;
  private responseCb: (msg: { text: string }) => void = () => {};
  private turnEndCb: () => void = () => {};
  private statusCb: (connected: boolean) => void = () => {};

  constructor(url = "ws://localhost:8000/ws") {
    this.url = url;
    this.connect();
  }

  /** Give the client the orb so it can play audio + drive states. */
  attachOrb(orb: OrbVisualizer): void {
    this.orb = orb;
  }

  private connect(): void {
    try {
      this.ws = new WebSocket(this.url);
    } catch (err) {
      console.warn("WebSocket construction failed:", err);
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.connected = true;
      this.reconnectAttempts = 0;
      this.statusCb(true);
      this.startPing();
    };

    this.ws.onclose = () => {
      this.connected = false;
      this.statusCb(false);
      this.stopPing();
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      // onclose will follow and handle reconnect.
      this.connected = false;
      this.statusCb(false);
    };

    this.ws.onmessage = (event) => {
      this.handleMessage(event.data);
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnect) {
      console.warn("Max reconnect attempts reached.");
      return;
    }
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 16000);
    this.reconnectAttempts += 1;
    setTimeout(() => this.connect(), delay);
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = window.setInterval(() => {
      if (this.connected && this.ws) {
        this.ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 25000);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private async handleMessage(raw: string): Promise<void> {
    let data: any;
    try {
      data = JSON.parse(raw);
    } catch {
      return;
    }

    if (data.type === "pong") return;

    // Text arrives first (before TTS) so the UI can react immediately.
    if (data.type === "response") {
      const msg = data as ResponseMessage;
      this.responseCb({ text: msg.text });
      return;
    }

    // Audio arrives as a follow-up; playing it (or not) ends the turn.
    if (data.type === "audio") {
      const audio = data.audio as string | null | undefined;
      if (audio && this.orb) {
        try {
          await this.playBase64Audio(audio);
        } catch (err) {
          console.warn("Audio playback failed:", err);
          this.orb.setState("idle");
        }
      } else {
        // No audio (TTS fell back to local `say` or failed) — settle the orb.
        this.orb?.setState("idle");
      }
      this.turnEndCb();
      return;
    }

    if (data.type === "error") {
      console.warn("Server error:", data.message);
      this.orb?.setState("idle");
      this.turnEndCb();
    }
  }

  /** Decode base64 MP3 → AudioBuffer (in the orb's context) → play. */
  private async playBase64Audio(b64: string): Promise<void> {
    if (!this.orb) return;
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

    const ctx = this.orb.audioContext;
    const buffer = await ctx.decodeAudioData(bytes.buffer);
    this.orb.playAudioBuffer(buffer);
  }

  /** Send a user message and flip the orb into the "thinking" state. */
  send(text: string): void {
    if (!this.connected || !this.ws) {
      console.warn("Not connected; message dropped.");
      return;
    }
    this.orb?.setState("thinking");
    this.ws.send(JSON.stringify({ type: "message", content: text }));
  }

  onResponse(cb: (msg: { text: string }) => void): void {
    this.responseCb = cb;
  }

  /** Fired when a turn fully completes (audio played, or none/error). */
  onTurnEnd(cb: () => void): void {
    this.turnEndCb = cb;
  }

  onStatus(cb: (connected: boolean) => void): void {
    this.statusCb = cb;
  }

  isConnected(): boolean {
    return this.connected;
  }
}
