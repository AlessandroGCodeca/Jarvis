/**
 * Speech-to-text via the Web Speech API.
 *
 * Emits interim transcripts as the user speaks and a final transcript when a
 * phrase completes. Gracefully no-ops if the browser lacks SpeechRecognition.
 */
export class VoiceInput {
  private recognition: any | null = null;
  private listening = false;
  private finalCb: (text: string) => void = () => {};
  private interimCb: (text: string) => void = () => {};
  private stateCb: (listening: boolean) => void = () => {};

  constructor() {
    const SR =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;

    if (!SR) {
      console.warn("Web Speech API not supported in this browser.");
      return;
    }

    const recognition = new SR();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event: any) => {
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          final += transcript;
        } else {
          interim += transcript;
        }
      }
      if (interim) this.interimCb(interim);
      if (final.trim()) this.finalCb(final.trim());
    };

    recognition.onend = () => {
      this.listening = false;
      this.stateCb(false);
    };

    recognition.onerror = (event: any) => {
      console.warn("SpeechRecognition error:", event.error);
      this.listening = false;
      this.stateCb(false);
    };

    this.recognition = recognition;
  }

  isSupported(): boolean {
    return this.recognition !== null;
  }

  start(): void {
    if (!this.recognition || this.listening) return;
    try {
      this.recognition.start();
      this.listening = true;
      this.stateCb(true);
    } catch (err) {
      console.warn("Could not start recognition:", err);
    }
  }

  stop(): void {
    if (!this.recognition || !this.listening) return;
    this.recognition.stop();
    this.listening = false;
    this.stateCb(false);
  }

  isListening(): boolean {
    return this.listening;
  }

  /** Register a callback for the final transcript. */
  onResult(cb: (text: string) => void): void {
    this.finalCb = cb;
  }

  /** Register a callback for interim (in-progress) transcripts. */
  onInterim(cb: (text: string) => void): void {
    this.interimCb = cb;
  }

  /** Register a callback for listening state changes. */
  onStateChange(cb: (listening: boolean) => void): void {
    this.stateCb = cb;
  }
}
