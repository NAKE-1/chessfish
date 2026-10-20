import { Chessground } from "./vendor/chessground/chessground.min.js";

const $ = (id) => document.getElementById(id);
const PROMO = ["q", "r", "b", "n"];
const PROMO_ROLE = { q: "queen", r: "rook", b: "bishop", n: "knight" };

// ---------- sounds (start only after a user gesture) ----------
const sounds = Object.fromEntries(
  ["move", "capture", "check", "castle", "start", "end", "lowtime"].map((n) => [n, new Audio(`sounds/${n}.wav`)]));
let muted = localStorage.getItem("muted") === "1";
let audioOk = false;
function play(name) {
  if (muted || !audioOk) return;
  const a = sounds[name];
  a.currentTime = 0;
  a.play().catch(() => {});
}
function setMuteIcon() { $("mute").textContent = muted ? "🔇" : "🔊"; }

// ---------- state ----------
let ws, name, pingId = 0, pingSent = {}, rtt = null, backoff = 500;
let st = null;              // last `state`
let clockBase = null;       // {white_ms, black_ms, running, at}
let countdownEnd = null, bannerEnd = null, bannerKind = null;
let drawOffered = false, lastOver = null, lowPlayed = false, flipped = false, orientation = "white";
let resignArmed = null;

const cg = Chessground($("board"), {
  movable: { free: false, color: undefined, showDests: true, events: { after: onUserMove } },
  premovable: { enabled: false },
  draggable: { showGhost: true },
  animation: { duration: 180 },
  coordinates: true,
});

// ---------- name entry ----------
$("name").value = localStorage.getItem("name") || "";
$("name-screen").addEventListener("submit", (e) => {
  e.preventDefault();
  name = $("name").value.trim();
  if (!name) return;
  localStorage.setItem("name", name);
  audioOk = true;
  $("name-screen").hidden = true;
  $("main").hidden = false;
  cg.redrawAll();
  connect();
});

// ---------- websocket ----------
function connect() {
  overlay("Connecting…");
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/play`);
  ws.onopen = () => { backoff = 500; send({ t: "hello", name }); };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = (e) => {
    setPing(null);
    if (e.code === 4001) return overlay("This game is open in another tab or device");
    overlay("Connection lost — reconnecting…");
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 10000);
  };
}
function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

setInterval(() => {
  pingSent[++pingId] = performance.now();
  send({ t: "ping", id: pingId, rtt });
}, 2000);

function handle(m) {
  switch (m.t) {
    case "waiting":
      st = null; clearGame();
      overlay("Waiting for game");
      break;
    case "countdown":
      st = null; clearGame();
      countdownEnd = performance.now() + m.seconds * 1000;
      break;
    case "state": onState(m); break;
    case "gameover": onGameOver(m); break;
    case "banner":
      bannerKind = m.kind;
      bannerEnd = m.seconds_left != null ? performance.now() + m.seconds_left * 1000 : null;
      break;
    case "move_rejected": toast(m.reason); break;
    case "draw_offered": drawOffered = true; sys("Opponent offers a draw"); updateButtons(); break;
    case "chat": chatLine(m.from, m.text); break;
    case "pong":
      if (pingSent[m.id] != null) { setPing(performance.now() - pingSent[m.id]); delete pingSent[m.id]; }
      break;
  }
}

function clearGame() {
  $("result").hidden = true; lastOver = null; drawOffered = false; lowPlayed = false;
  $("moves").innerHTML = ""; clockBase = null; renderClocks();
}

function onState(m) {
  const prev = st;
  st = m;
  countdownEnd = null;
  $("overlay").hidden = true;
  if (!prev) {
    if (!flipped) orientation = m.your_color;
    if (m.moves_san.length === 0) play("start");
  } else if (m.moves_san.length === prev.moves_san.length + 1) {
    const san = m.moves_san[m.moves_san.length - 1];
    play(san.includes("+") || san.includes("#") ? "check" : san.startsWith("O-O") ? "castle" : san.includes("x") ? "capture" : "move");
    drawOffered = false;
  }
  const myTurn = m.turn === m.your_color && Object.keys(m.dests).length > 0;
  cg.set({
    fen: m.fen,
    orientation,
    turnColor: m.turn,
    lastMove: m.last_move || undefined,
    check: m.check ? m.turn : false,
    movable: { color: myTurn ? m.your_color : undefined, dests: new Map(Object.entries(m.dests)) },
  });
  $("promo").hidden = true;
  clockBase = { ...m.clocks, at: performance.now() };
  renderMoves(m.moves_san);
  updateButtons();
}

function onGameOver(m) {
  const key = `${m.result}/${m.reason}/${st ? st.moves_san.length : 0}`;
  if (lastOver === key) return;
  lastOver = key;
  play("end");
  const me = st ? st.your_color : null;
  const win = (m.result === "1-0" && me === "white") || (m.result === "0-1" && me === "black");
  const text = m.result === "1/2-1/2" ? "Draw" : m.result === "*" ? "Game aborted" : win ? "You won" : "You lost";
  const reason = m.reason.replaceAll("_", " ");
  $("result").textContent = m.result === "*" ? text : `${text} — ${reason} (${m.result})`;
  $("result").hidden = false;
  cg.set({ movable: { color: undefined } });
  updateButtons();
}

// ---------- moves ----------
function onUserMove(orig, dest) {
  const piece = cg.state.pieces.get(dest);
  if (piece && piece.role === "pawn" && (dest[1] === "8" || dest[1] === "1")) return askPromotion(orig, dest);
  send({ t: "move", uci: orig + dest });
}

function askPromotion(orig, dest) {
  const box = $("promo");
  box.innerHTML = "";
  for (const p of PROMO) {
    const b = document.createElement("button");
    b.type = "button";
    b.title = PROMO_ROLE[p];
    b.innerHTML = `<piece class="${PROMO_ROLE[p]} ${st.your_color}"></piece>`;
    b.onclick = () => { box.hidden = true; send({ t: "move", uci: orig + dest + p }); };
    box.appendChild(b);
  }
  box.hidden = false;
}

function renderMoves(sans) {
  const ol = $("moves");
  ol.innerHTML = "";
  for (let i = 0; i < sans.length; i += 2) {
    const li = document.createElement("li");
    li.innerHTML = `<span></span><span></span>`;
    li.children[0].textContent = sans[i];
    li.children[1].textContent = sans[i + 1] || "";
    ol.appendChild(li);
  }
  ol.scrollTop = ol.scrollHeight;
}

// ---------- clocks / overlays (animation loop) ----------
function fmt(ms) {
  if (ms == null) return "–";
  ms = Math.max(0, ms);
  const s = ms / 1000;
  if (s < 10) return s.toFixed(1);
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

function renderClocks() {
  const me = st ? st.your_color : "white", opp = me === "white" ? "black" : "white";
  const bottom = orientation === me ? me : opp, top = bottom === me ? opp : me;
  $("bottom-name").textContent = bottom === me ? `${name || "You"}` : "Opponent";
  $("top-name").textContent = top === me ? `${name || "You"}` : "Opponent";
  for (const [el, color] of [[$("top-clock"), top], [$("bottom-clock"), bottom]]) {
    if (!clockBase || clockBase[`${color}_ms`] == null) { el.textContent = "–"; el.className = "clock"; continue; }
    let ms = clockBase[`${color}_ms`];
    const running = clockBase.running === color && !lastOver;
    if (running) ms -= performance.now() - clockBase.at;
    el.textContent = fmt(ms);
    el.className = "clock" + (running ? " active" : "") + (ms < 10000 ? " low" : "");
    if (color === me && running && ms < 10000 && !lowPlayed) { lowPlayed = true; play("lowtime"); }
  }
}

function frame() {
  renderClocks();
  if (countdownEnd != null) {
    const left = Math.ceil((countdownEnd - performance.now()) / 1000);
    overlay(left > 0 ? `Game starts in<span class="big">${left}</span>` : "Starting…", true);
  }
  const b = $("banner");
  if (bannerKind === "opponent_reconnecting") {
    const left = bannerEnd ? Math.max(0, Math.ceil((bannerEnd - performance.now()) / 1000)) : 0;
    b.textContent = `Opponent reconnecting… ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    b.hidden = false;
  } else if (bannerKind === "paused") {
    b.textContent = "Game paused"; b.hidden = false;
  } else if (bannerKind === "adjourned") {
    b.textContent = "Game adjourned — it can be resumed later"; b.hidden = false;
  } else b.hidden = true;
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

function overlay(html, isHtml) {
  const el = $("overlay-text");
  if (isHtml) el.innerHTML = html; else el.textContent = html;
  $("overlay").hidden = false;
}

// ---------- buttons ----------
function live() { return st && !lastOver && Object.prototype.hasOwnProperty.call(st, "dests") && bannerKind !== "adjourned"; }
function updateButtons() {
  $("resign").disabled = !live();
  $("draw").disabled = !live();
  $("draw").textContent = drawOffered ? "Accept draw" : "Offer draw";
  $("draw").classList.toggle("offered", drawOffered);
}
$("resign").onclick = () => {
  if (!resignArmed) {
    $("resign").textContent = "Confirm resign?";
    $("resign").classList.add("confirm");
    resignArmed = setTimeout(disarm, 3000);
    return;
  }
  disarm();
  send({ t: "resign" });
};
function disarm() {
  clearTimeout(resignArmed); resignArmed = null;
  $("resign").textContent = "Resign"; $("resign").classList.remove("confirm");
}
$("draw").onclick = () => {
  send({ t: drawOffered ? "draw_accept" : "draw_offer" });
  if (!drawOffered) sys("Draw offered");
};
$("flip").onclick = () => {
  flipped = true;
  orientation = orientation === "white" ? "black" : "white";
  cg.set({ orientation });
};
$("mute").onclick = () => { muted = !muted; localStorage.setItem("muted", muted ? "1" : "0"); setMuteIcon(); };
setMuteIcon();

// ---------- chat ----------
$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text) return;
  send({ t: "chat", text });
  $("chat-input").value = "";
});
function chatLine(from, text) {
  const div = document.createElement("div");
  const who = document.createElement("span");
  who.className = "from";
  who.textContent = from === "You" ? `${name}:` : "Opponent:";
  div.append(who, document.createTextNode(text));
  appendChat(div);
}
function sys(text) {
  const div = document.createElement("div");
  div.className = "sys";
  div.textContent = text;
  appendChat(div);
}
function appendChat(div) {
  const log = $("chat-log");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// ---------- misc ----------
function setPing(ms) {
  rtt = ms == null ? null : Math.round(ms);
  const el = $("ping");
  el.textContent = rtt == null ? "offline" : `${rtt} ms`;
  el.className = "ping " + (rtt == null ? "bad" : rtt < 120 ? "ok" : rtt < 300 ? "warn" : "bad");
}
let toastTimer;
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 2500);
}
