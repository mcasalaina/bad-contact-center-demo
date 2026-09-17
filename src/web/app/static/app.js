const $ = (id) => document.getElementById(id);

const VOICE_OPTIONS = {
  "gpt-realtime-2.1": [
    ["coral", "Coral"],
    ["ballad", "Ballad"],
    ["marin", "Marin"],
    ["sage", "Sage"],
    ["shimmer", "Shimmer"],
    ["alloy", "Alloy"],
    ["ash", "Ash"],
    ["cedar", "Cedar"],
    ["echo", "Echo"],
    ["verse", "Verse"],
  ],
  "azure-realtime": [
    ["ava", "Ava (US English)"],
    ["andrew", "Andrew (US English)"],
    ["aarti", "Aarti (Indian English)"],
    ["denise", "Denise (French)"],
    ["diya", "Diya (Hindi / English)"],
    ["elsa", "Elsa (Italian)"],
    ["florian", "Florian (German)"],
    ["francisca", "Francisca (Brazilian Portuguese)"],
    ["meera", "Meera (Hindi / English)"],
    ["xiaoxiao", "Xiaoxiao (Mandarin)"],
    ["ximena", "Ximena (Spanish)"],
    ["yunxi", "Yunxi (Mandarin)"],
  ],
};

let socket;
let audioContext;
let mediaStream;
let microphoneSource;
let microphoneProcessor;
let mixedCapture;
let playbackTime = 0;
let toolTimer;
let toolStartedAt = 0;
let toolActive = false;
let stopping = false;
let recorders = [];
let recordingUrl;
const playbackNodes = new Set();

function stamp() {
  return new Date().toLocaleTimeString([], { hour12: false });
}

function updateVoiceOptions() {
  const voice = $("voice");
  voice.replaceChildren();
  for (const [value, label] of VOICE_OPTIONS[$("model").value]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    voice.appendChild(option);
  }
}

function addEntry(target, kind, text) {
  const box = $(target);
  if (box.classList.contains("empty")) {
    box.textContent = "";
    box.classList.remove("empty");
  }
  const row = document.createElement("div");
  row.className = `entry ${kind}`;
  const time = document.createElement("time");
  time.textContent = stamp();
  row.append(time, document.createTextNode(text));
  box.appendChild(row);
  box.scrollTop = box.scrollHeight;
}

function setTool(state, elapsed = 0) {
  $("tool-state").textContent = state;
  $("elapsed").textContent = elapsed.toFixed(1);
  $("progress").style.width = `${Math.min(100, elapsed / 6 * 100)}%`;
}

function startToolTimer() {
  toolActive = true;
  toolStartedAt = performance.now();
  clearInterval(toolTimer);
  toolTimer = setInterval(() => {
    setTool("Waiting in silence", Math.min(6, (performance.now() - toolStartedAt) / 1000));
  }, 100);
}

function stopToolTimer(state) {
  toolActive = false;
  clearInterval(toolTimer);
  setTool(state, state === "Complete" ? 6 : 0);
}

function stopQueuedSystemAudio() {
  for (const node of playbackNodes) {
    try {
      node.stop();
    } catch {
      // The source may already have stopped.
    }
  }
  playbackNodes.clear();
  playbackTime = audioContext?.currentTime || 0;
}

function downsample(input, inputRate, outputRate = 24000) {
  if (inputRate === outputRate) return input;
  const ratio = inputRate / outputRate;
  const output = new Float32Array(Math.round(input.length / ratio));
  for (let i = 0; i < output.length; i++) {
    output[i] = input[Math.floor(i * ratio)];
  }
  return output;
}

function pcm16(float32) {
  const output = new ArrayBuffer(float32.length * 2);
  const view = new DataView(output);
  float32.forEach((sample, i) => {
    const value = Math.max(-1, Math.min(1, sample));
    view.setInt16(i * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
  });
  return output;
}

function recorderMimeType() {
  return [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
  ].find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function createRecorder(stream, downloadId, filename) {
  const chunks = [];
  const options = recorderMimeType() ? { mimeType: recorderMimeType() } : undefined;
  const recorder = new MediaRecorder(stream, options);
  const finished = new Promise((resolve) => {
    recorder.ondataavailable = ({ data }) => {
      if (data.size) chunks.push(data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      const url = URL.createObjectURL(blob);
      if (recordingUrl) URL.revokeObjectURL(recordingUrl);
      recordingUrl = url;
      const link = $(downloadId);
      link.href = url;
      link.download = filename.replace(".webm", recorder.mimeType.includes("mp4") ? ".mp4" : ".webm");
      link.classList.remove("disabled");
      link.removeAttribute("aria-disabled");
      resolve();
    };
  });
  recorder.start(250);
  return { recorder, finished };
}

async function startAudioAndRecording() {
  audioContext = new AudioContext();
  await audioContext.resume();
  playbackTime = audioContext.currentTime;
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
  });

  microphoneSource = audioContext.createMediaStreamSource(mediaStream);
  microphoneProcessor = audioContext.createScriptProcessor(4096, 1, 1);
  const silentGain = audioContext.createGain();
  silentGain.gain.value = 0;
  microphoneSource.connect(microphoneProcessor);
  microphoneProcessor.connect(silentGain);
  silentGain.connect(audioContext.destination);
  microphoneProcessor.onaudioprocess = (event) => {
    if (toolActive || socket?.readyState !== WebSocket.OPEN) return;
    const samples = event.inputBuffer.getChannelData(0);
    socket.send(pcm16(downsample(samples, audioContext.sampleRate)));
  };

  mixedCapture = audioContext.createMediaStreamDestination();
  microphoneSource.connect(mixedCapture);

  const date = new Date().toISOString().replaceAll(":", "-").replace(/\..+/, "");
  recorders = [
    createRecorder(
      mixedCapture.stream,
      "mixed-download",
      `lyrenza-call-${date}.webm`,
    ),
  ];
}

function connectOutput(node) {
  node.connect(audioContext.destination);
  node.connect(mixedCapture);
}

function playRingTone() {
  return new Promise((resolve) => {
    const start = audioContext.currentTime + 0.05;
    const end = start + 1.35;
    const gain = audioContext.createGain();
    gain.gain.setValueAtTime(0, start);
    gain.gain.linearRampToValueAtTime(0.13, start + 0.03);
    gain.gain.setValueAtTime(0.13, start + 0.52);
    gain.gain.linearRampToValueAtTime(0, start + 0.57);
    gain.gain.setValueAtTime(0, start + 0.78);
    gain.gain.linearRampToValueAtTime(0.13, start + 0.81);
    gain.gain.setValueAtTime(0.13, end - 0.04);
    gain.gain.linearRampToValueAtTime(0, end);
    connectOutput(gain);

    for (const frequency of [440, 480]) {
      const oscillator = audioContext.createOscillator();
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      oscillator.connect(gain);
      oscillator.start(start);
      oscillator.stop(end);
    }
    setTimeout(resolve, 1450);
  });
}

function playAudio(frame) {
  if (!audioContext || frame.byteLength <= 8 || toolActive) return;
  const header = new DataView(frame, 0, 8);
  const sampleRate = header.getUint32(0, true);
  const channels = header.getUint32(4, true);
  const pcm = new Int16Array(frame, 8);
  const buffer = audioContext.createBuffer(channels, pcm.length / channels, sampleRate);
  for (let channel = 0; channel < channels; channel++) {
    const data = buffer.getChannelData(channel);
    for (let i = 0; i < data.length; i++) {
      data[i] = pcm[i * channels + channel] / 32768;
    }
  }
  const node = audioContext.createBufferSource();
  node.buffer = buffer;
  connectOutput(node);
  playbackNodes.add(node);
  node.onended = () => playbackNodes.delete(node);
  playbackTime = Math.max(playbackTime, audioContext.currentTime);
  node.start(playbackTime);
  playbackTime += buffer.duration;
}

function handleEvent(event) {
  switch (event.type) {
    case "session_started":
      $("connection").textContent = "Connected";
      addEntry("timeline", "bot", `Jolene answered · ${event.voice} · ${event.model}`);
      break;
    case "transcription":
      addEntry("conversation", "user", `Caller: ${event.text}`);
      break;
    case "bot_text":
      if (event.text) addEntry("conversation", "bot", `Jolene: ${event.text}`);
      break;
    case "tool_started":
      stopQueuedSystemAudio();
      startToolTimer();
      addEntry("timeline", "tool", "Pool-hours lookup started. Jolene is silent for six seconds.");
      break;
    case "tool_completed":
      stopToolTimer("Complete");
      addEntry("timeline", "tool", "Pool-hours lookup completed.");
      break;
    case "tool_failed":
    case "error":
      stopToolTimer("Failed");
      addEntry("timeline", "tool", `${event.type}: ${event.message}`);
      break;
    default:
      if (event.type === "user_speech_started" || event.type === "user_speech_stopped") {
        addEntry("timeline", "user", event.type.replaceAll("_", " "));
      }
  }
}

async function openSocket() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const currentSocket = new WebSocket(`${scheme}://${location.host}/ws`);
  socket = currentSocket;
  currentSocket.binaryType = "arraybuffer";
  await new Promise((resolve, reject) => {
    currentSocket.onopen = resolve;
    currentSocket.onerror = () => reject(new Error("The voice connection could not be opened."));
  });
  currentSocket.send(JSON.stringify({
    type: "start",
    model: $("model").value,
    voice: $("voice").value,
  }));
  currentSocket.onmessage = async ({ data }) => {
    if (data instanceof ArrayBuffer) playAudio(data);
    else handleEvent(JSON.parse(data));
  };
  currentSocket.onclose = () => {
    if (socket === currentSocket && !stopping) stop(false);
  };
}

async function start() {
  $("start").disabled = true;
  $("model").disabled = true;
  $("voice").disabled = true;
  $("connection").textContent = "Calling...";
  await startAudioAndRecording();
  addEntry("timeline", "bot", "Remote phone ringing.");
  await playRingTone();
  await openSocket();
  $("stop").disabled = false;
}

async function stop(closeSocket = true) {
  if (stopping) return;
  stopping = true;
  const currentSocket = socket;
  const currentAudioContext = audioContext;
  const currentMediaStream = mediaStream;
  const currentMicrophoneProcessor = microphoneProcessor;
  const currentMicrophoneSource = microphoneSource;
  const currentRecorders = recorders;

  socket = undefined;
  audioContext = undefined;
  mediaStream = undefined;
  microphoneProcessor = undefined;
  microphoneSource = undefined;
  mixedCapture = undefined;
  recorders = [];

  try {
    if (closeSocket) currentSocket?.close();
    clearInterval(toolTimer);
    toolActive = false;
    currentMicrophoneProcessor?.disconnect();
    currentMicrophoneSource?.disconnect();
    currentMediaStream?.getTracks().forEach((track) => track.stop());
    for (const { recorder } of currentRecorders) {
      if (recorder.state !== "inactive") recorder.stop();
    }
    await Promise.all(currentRecorders.map(({ finished }) => finished));
    if (currentRecorders.length) {
      $("recording-downloads").classList.remove("hidden");
    }
    playbackNodes.clear();
    await currentAudioContext?.close();
    $("connection").textContent = "Call ended";
    addEntry("timeline", "bot", "Recording is ready to download.");
  } finally {
    $("start").disabled = false;
    $("model").disabled = false;
    $("voice").disabled = false;
    $("stop").disabled = true;
    stopping = false;
  }
}

$("start").addEventListener("click", () => start().catch(async (error) => {
  addEntry("timeline", "tool", `Start failed: ${error.message}`);
  await stop();
}));
$("stop").addEventListener("click", () => stop());
$("model").addEventListener("change", updateVoiceOptions);
