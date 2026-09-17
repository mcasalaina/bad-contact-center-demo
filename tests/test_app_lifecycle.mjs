import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

class FakeClassList {
  constructor(...names) {
    this.names = new Set(names);
  }

  add(name) {
    this.names.add(name);
  }

  contains(name) {
    return this.names.has(name);
  }

  remove(name) {
    this.names.delete(name);
  }
}

function element({ value = "", disabled = false, classes = [] } = {}) {
  return {
    value,
    disabled,
    classList: new FakeClassList(...classes),
    listeners: {},
    append() {},
    appendChild() {},
    removeAttribute(name) {
      delete this[name];
    },
    replaceChildren() {},
    setAttribute(name, value) {
      this[name] = value;
    },
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    },
    textContent: "",
    style: {},
    scrollHeight: 0,
    scrollTop: 0,
  };
}

class FakeAudioNode {
  connect() {}
  disconnect() {}
  start() {}
  stop() {}
}

class FakeAudioContext {
  constructor() {
    this.currentTime = 0;
    this.destination = {};
    this.sampleRate = 48000;
  }

  async close() {}
  async resume() {}
  createMediaStreamSource() {
    return new FakeAudioNode();
  }
  createScriptProcessor() {
    return new FakeAudioNode();
  }
  createGain() {
    const node = new FakeAudioNode();
    node.gain = {
      value: 0,
      setValueAtTime() {},
      linearRampToValueAtTime() {},
    };
    return node;
  }
  createMediaStreamDestination() {
    const node = new FakeAudioNode();
    node.stream = {};
    return node;
  }
  createOscillator() {
    const node = new FakeAudioNode();
    node.frequency = {};
    return node;
  }
}

class FakeMediaRecorder {
  static isTypeSupported() {
    return true;
  }

  constructor() {
    this.mimeType = "audio/webm";
    this.state = "inactive";
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable({ data: new Blob(["recording"]) });
    this.onstop();
  }
}

class FakeWebSocket {
  static OPEN = 1;
  static instances = [];

  constructor() {
    this.readyState = 0;
    FakeWebSocket.instances.push(this);
    queueMicrotask(() => {
      this.readyState = FakeWebSocket.OPEN;
      this.onopen();
    });
  }

  close() {
    this.readyState = 3;
  }

  fireClose() {
    this.onclose();
  }

  send() {}
}

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 50; attempt++) {
    if (predicate()) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.fail("Timed out waiting for browser lifecycle state");
}

test("two calls stop independently and the latest ended call owns the download", async () => {
  const elements = {
    connection: element(),
    conversation: element(),
    elapsed: element(),
    model: element({ value: "gpt-realtime-2.1" }),
    "mixed-download": element({ classes: ["disabled"] }),
    progress: element(),
    "recording-downloads": element({ classes: ["hidden"] }),
    start: element(),
    stop: element({ disabled: true }),
    timeline: element(),
    "tool-state": element(),
    voice: element({ value: "coral" }),
  };
  const urls = [];
  const revokedUrls = [];
  const mediaStream = {
    getTracks: () => [{ stop() {} }],
  };
  const context = {
    AudioContext: FakeAudioContext,
    Blob,
    DataView,
    Float32Array,
    Int16Array,
    MediaRecorder: FakeMediaRecorder,
    URL: {
      createObjectURL() {
        const url = `blob:call-${urls.length + 1}`;
        urls.push(url);
        return url;
      },
      revokeObjectURL(url) {
        revokedUrls.push(url);
      },
    },
    WebSocket: FakeWebSocket,
    clearInterval() {},
    document: {
      createElement: () => element(),
      createTextNode: () => ({}),
      getElementById: (id) => elements[id],
    },
    location: { host: "localhost:8080", protocol: "http:" },
    navigator: {
      mediaDevices: {
        getUserMedia: async () => mediaStream,
      },
    },
    performance: { now: () => 0 },
    queueMicrotask,
    setInterval: () => 1,
    setTimeout: (callback) => callback(),
  };

  vm.runInNewContext(
    fs.readFileSync("src/web/app/static/app.js", "utf8"),
    context,
  );

  elements.start.listeners.click();
  await waitFor(() => elements.stop.disabled === false);
  const firstSocket = FakeWebSocket.instances[0];
  elements.stop.listeners.click();
  await waitFor(() => elements.start.disabled === false);
  assert.equal(elements["mixed-download"].href, "blob:call-1");

  elements.start.listeners.click();
  await waitFor(() => elements.stop.disabled === false);
  assert.equal(elements["mixed-download"].href, "blob:call-1");

  firstSocket.fireClose();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(elements.stop.disabled, false);

  elements.stop.listeners.click();
  await waitFor(() => elements.start.disabled === false);
  assert.equal(elements.stop.disabled, true);
  assert.equal(elements["mixed-download"].href, "blob:call-2");
  assert.deepEqual(revokedUrls, ["blob:call-1"]);
});
