// Existing HTML Audio player, controlled only by the private local bridge.
let audio = null, session = null, applied = null, action = 'idle';
let lostAt = null, connectionPaused = false;
let sending = false, retry = null, deliveryFailed = false;
const pending = [];
const base = location.pathname;
const GRACE_MS = 5000;
const request = (path, options = {}) => fetch(base + path, {...options, signal: AbortSignal.timeout(2000)});

function report(kind, id, error = null, source = audio) {
  if (!id) return;
  const event = {kind, session: id, error,
    position_seconds: source && Number.isFinite(source.currentTime) ? source.currentTime : 0,
    media_error_code: source?.error?.code ?? null};
  // Coalesce routine progress during an outage instead of building a backlog.
  const existing = pending.findIndex(item => item.kind === kind && item.session === id);
  if (existing >= 0) {
    if (!sending || existing !== 0) pending[existing] = event;
  } else pending.push(event);
  flushEvents();
}

async function flushEvents() {
  if (sending || retry || !pending.length) return;
  sending = true;
  let failed = false;
  try {
    const response = await request('event', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(pending[0])});
    if (!response.ok) throw Error('Audio event was not acknowledged');
    pending.shift();
    deliveryFailed = false;
  } catch {
    failed = true;
    if (!deliveryFailed) console.error('Station audio events could not reach the backend; retrying.');
    deliveryFailed = true;
    // Keep terminal events until acknowledged; the backend accepts duplicates.
  } finally {
    sending = false;
    if (pending.length) retry = setTimeout(() => {retry=null;flushEvents();}, failed ? 1000 : 0);
  }
}

function clearAudio() {
  const previous = audio;
  audio = null;
  if (previous) {previous.pause();previous.removeAttribute('src');previous.load();}
}

function play(command) {
  clearAudio();
  connectionPaused = false;
  session = command.session;
  const id = session, current = new Audio();
  let hasPlayed = false;
  audio = current;
  current.preload = 'auto';
  current.crossOrigin = 'anonymous';
  current.addEventListener('playing', () => {hasPlayed=true;report('playing',id,null,current);});
  for (const kind of ['waiting','ended']) current.addEventListener(kind, () => report(kind,id,null,current));
  current.addEventListener('error', () => report('error',id,
    'The browser could not decode or load this audio (media error '+(current.error?.code ?? 'unknown')+').',current));
  current.addEventListener('timeupdate', () => report('progress',id,null,current));
  current.addEventListener('pause', () => {
    if (current === audio && action === 'play' && hasPlayed && !current.ended && !connectionPaused)
      report('error',id,'The browser paused the song unexpectedly.',current);
  });
  if (Number.isFinite(command.resume_seconds) && command.resume_seconds > 0) {
    current.addEventListener('loadedmetadata', () => {
      if (current === audio && Number.isFinite(current.duration))
        current.currentTime = Math.min(command.resume_seconds, Math.max(0,current.duration-.05));
    }, {once:true});
  }
  current.src = command.src;
  current.load();
  current.play().catch(() => report('error',id,'The browser could not start audio playback.',current));
}

async function poll() {
  try {
    const response = await request('command');
    if (!response.ok) throw Error('Audio command unavailable');
    const command = await response.json();
    if (!Number.isInteger(command.revision) || !['play','stop','idle'].includes(command.action))
      throw Error('Invalid audio command');
    const recovering = lostAt !== null;
    lostAt = null;
    action = command.action;
    if (command.revision !== applied) {
      applied = command.revision;
      if (action === 'play') play(command);
      else {
        if (action === 'stop') report('stopped',command.session);
        clearAudio();
        connectionPaused = false;
        session = null;
      }
    } else if (connectionPaused && action === 'play' && session === command.session && audio && !audio.ended) {
      connectionPaused = false;
      audio.play().catch(() => report('error',session,'Audio could not resume after reconnecting.'));
    }
    if (recovering) report('control_restored',session);
  } catch {
    if (lostAt === null) {
      lostAt = performance.now();
      console.warn('Station audio control connection lost; allowing a brief recovery interval.');
      report('control_lost',session,'The local audio control connection was lost; trying to reconnect.');
    }
    if (audio && !audio.ended && !connectionPaused && performance.now()-lostAt >= GRACE_MS) {
      connectionPaused = true;
      audio.pause();
      report('control_paused',session,'Audio paused while waiting for the local control connection.');
    }
  }
  setTimeout(poll,250);
}
// This still runs when audio is buffering, allowing the backend to detect a
// missing/frozen browser independently of progress or a final error event.
setInterval(() => report('heartbeat',session),1000);
poll();
