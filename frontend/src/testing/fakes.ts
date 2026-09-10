export class FakeClock {
  private currentTime = 0;

  now() {
    return this.currentTime;
  }

  advanceBy(milliseconds: number) {
    this.currentTime += milliseconds;
  }
}

export class FakeNetwork {
  requests: string[] = [];
  online = true;

  async fetch(url: string) {
    this.requests.push(url);
    if (!this.online) {
      throw new TypeError('network unavailable');
    }
    return { ok: true, status: 200 };
  }
}

export class FakeSecureStorage {
  private values = new Map<string, string>();

  async getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  async setItem(key: string, value: string) {
    this.values.set(key, value);
  }

  async removeItem(key: string) {
    this.values.delete(key);
  }
}

export class FakeVoiceModule {
  startCalls = 0;
  stopCalls = 0;

  async startMicrophone() {
    this.startCalls += 1;
  }

  async stopMicrophone() {
    this.stopCalls += 1;
  }
}

export class FakeWebSocket {
  readonly sent: string[] = [];
  readyState = 1;

  send(message: string) {
    this.sent.push(message);
  }

  close() {
    this.readyState = 3;
  }
}
