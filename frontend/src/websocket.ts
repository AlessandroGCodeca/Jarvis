import type { OrbVisualizer } from "./orb";

interface ResponseMessage {
  // "response" = reply to the user; "proactive" = server-initiated (idle mood).
  type: "response" | "proactive";
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
  private pingTimer: number | null = null;

  // Per-turn generation counter. Bumped when a new message is sent or playback
  // is interrupted, so late-arriving audio for a stale turn is discarded.
  private gen = 0;
  // Serializes async audio decoding so chunks play in arrival order.
  private decodeChain: Promise<void> = Promise.resolve();

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
    // The turn ends when the orb finishes playing all of this turn's audio.
    orb.setPlaybackCompleteHandler(() => this.turnEndCb());
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
    // Keep retrying forever — this is an always-on assistant. Exponential
    // backoff, capped at 30s between attempts.
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30000);
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
    // "proactive" (idle-mood phrases) follows the same display+audio path.
    if (data.type === "response" || data.type === "proactive") {
      const msg = data as ResponseMessage;
      this.responseCb({ text: msg.text });
      return;
    }

    // Audio chunks arrive one sentence at a time. Decode them on a serialized
    // chain so they're enqueued in arrival order, and tag each with the current
    // generation so a barge-in / new turn discards stale chunks.
    if (data.type === "audio") {
      const audio = data.audio as string | null | undefined;
      if (audio && this.orb) {
        const myGen = this.gen;
        this.decodeChain = this.decodeChain.then(async () => {
          if (myGen !== this.gen || !this.orb) return;
          try {
            const buffer = await this.decodeBase64Audio(audio);
            if (myGen !== this.gen || !this.orb) return;
            this.orb.enqueueAudio(buffer);
          } catch (err) {
            console.warn("Audio decode failed:", err);
          }
        });
      }
      return;
    }

    // End of this turn's audio stream — mark it after pending decodes resolve.
    if (data.type === "audio_end") {
      const myGen = this.gen;
      this.decodeChain = this.decodeChain.then(() => {
        if (myGen !== this.gen || !this.orb) return;
        this.orb.markAudioStreamEnd();
      });
      return;
    }

    if (data.type === "error") {
      console.warn("Server error:", data.message);
      this.stopPlayback(); // invalidate any in-flight audio for this turn
      this.turnEndCb();
    }
  }

  /** Decode base64 MP3 → AudioBuffer in the orb's shared AudioContext. */
  private async decodeBase64Audio(b64: string): Promise<AudioBuffer> {
    if (!this.orb) throw new Error("orb not attached");
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return this.orb.audioContext.decodeAudioData(bytes.buffer);
  }

  /** Stop current playback + discard queued/in-flight audio (barge-in). */
  stopPlayback(): void {
    this.gen += 1; // invalidate any pending decodes / audio_end for this turn
    this.orb?.stopPlayback();
  }

  /** Send a user message and flip the orb into the "thinking" state. */
  send(text: string): void {
    if (!this.connected || !this.ws) {
      console.warn("Not connected; message dropped.");
      return;
    }
    this.gen += 1; // new turn
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
