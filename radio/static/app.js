import {$, api, node, notify, openDialog, showError, statsNode} from './ui.js';
import {renderMessages, renderPolicies, renderImport, scrollConversation} from './chat.js';
import {CatalogEditor} from './catalog.js';

let state = null, polling = false, importing = false, sending = false, connecting = false, programmingAction = false, queueAction = false, pendingQueueSong = null, queueFeedback = null, nowPlayingId = null;
const editor = new CatalogEditor(refresh);
const policyReview = $('step-policy-review') || document.createElement('button');
policyReview.id = 'step-policy-review';
policyReview.type = 'button';
policyReview.className = 'text-button';
policyReview.textContent = 'Review policy proposals';
policyReview.hidden = true;
if (!policyReview.isConnected) $('step-action').after(policyReview);
const stepContent = {
  connection: ['Connect your host', 'Connect your ChatGPT account to talk with your radio host. Sign in on the official page, then return here.', 'Connect ChatGPT'],
  music: ['Import your music', 'Your host is connected. Next, let’s collect some songs from the community. Paste the copied Discord posts and we’ll check them together.', 'Import community music'],
  catalog: ['Get to know your catalog', 'Review the imported songs and artist names. You can edit details directly, and your host can help you make sense of the library.', 'Review music'],
  policies: ['Shape your station together', 'Talk with your host about ordinary programming. Review and manage proposals from the Policies button beside Now Playing.', 'Discuss station defaults'],
  finish: ['Your station is ready', 'Your music has been reviewed and your default policies are approved. You can finish setup and keep talking with your host.', 'Finish setup']
};
function busyState() {
  const busy = importing || sending || state?.busy;
  $('send').disabled = !state?.authenticated || !!busy || !!state?.login?.pending;
  $('message').placeholder = state?.authenticated ? 'Message your radio host…' : 'Connect your host to start chatting…';
  $('processing').hidden = !busy;
  $('processing-text').textContent = importing ? 'Importing community music…' : 'Your host is thinking…';
  document.querySelectorAll('#open-import, #station-import').forEach(button => { button.disabled = importing; });
  const program = state?.station?.programming;
  const queueEnabled = !!program?.running && !program.stop_requested && !state?.busy && !queueAction;
  document.querySelectorAll('#queue-song-next, #queue-song-append').forEach(button => { button.disabled = !queueEnabled; });
  const feedback = queueFeedback && editor.selected
    && queueFeedback.id === editor.selected.song.suno_song_id ? queueFeedback.text : null;
  $('queue-song-status').textContent = queueAction ? 'Sending your request to the host…'
    : feedback || (program?.stop_requested ? 'The station is signing off. Start a new program to queue songs.'
    : state?.busy ? 'Your host is busy. Wait for the current response to finish.'
    : program?.running ? 'The host will prepare a brief introduction and outro.'
    : 'Start Programming to add songs to the live queue.');
}
function renderContext(station) {
  if (!station) return;
  $('station-status').textContent = 'Station: ' + station.station_status;
  $('station-dot').classList.toggle('playing', station.playback_state === 'playing');
  const program = station.programming;
  $('start-programming').disabled = !program?.ready || program.running || programmingAction || !!state?.busy;
  $('stop-programming').disabled = !program?.running || program.stop_requested || programmingAction;
  $('programming-note').textContent = !program?.available ? 'Programming is not available in this session'
    : program.stop_requested ? 'Finishing the current song, then signing off'
    : program.running ? (program.playback_error || (program.phase === 'planning' ? 'Your host is preparing the program…' : 'Live · changes can be requested in chat'))
    : program.error || (program.ready ? 'Ready to go on air' : 'Complete station setup to begin');
  $('onboarding').hidden = station.onboarding.complete;
  $('station-panels').hidden = !station.onboarding.complete;
  const current = station.onboarding.current;
  $('step-policy-review').hidden = current !== 'policies';
  $('setup-steps').replaceChildren(...station.onboarding.steps.map((step, index) => {
    const item = node('li', null, step.complete ? 'done' : step.id === current ? 'current' : '');
    item.append(node('span', step.complete ? '✓' : index + 1, 'step-marker'), node('span', step.title));
    if (step.id === current) item.setAttribute('aria-current', 'step');
    return item;
  }));
  const [title, description, action] = stepContent[current];
  $('step-title').textContent = title; $('step-description').textContent = description; $('step-action').textContent = action;
  $('step-action').disabled = connecting || importing;
  $('setup-note').textContent = current === 'catalog' && station.onboarding.unresolved_artists
    ? station.onboarding.unresolved_artists + ' artist assignments or names need your review.'
    : 'You can keep talking with your host at every step.';
  if (station.onboarding.complete) {
    $('catalog-summary').replaceChildren(...[[station.catalog.youtube_tracks ? 'Suno songs' : 'Songs', station.catalog.songs], ['Artists', station.catalog.artists], ...(station.catalog.youtube_tracks ? [['YouTube', station.catalog.youtube_tracks]] : [])].map(([label, value]) => {
      const item = node('div', null, 'summary-item'); item.append(node('strong', value), node('span', label)); return item;
    }));
    $('playback-badge').textContent = station.now_playing ? station.station_status : 'Idle';
    const now = station.now_playing;
    if (now) {
      nowPlayingId = now.song.suno_song_id;
      const body = node('div', null, 'now-playing-layout');
      if (now.artwork_url) {
        const image = node('img', null, 'now-playing-artwork'); image.src = now.artwork_url;
        image.alt = 'Artwork for ' + (now.song.title || 'the current song'); body.append(image);
      } else body.append(node('span', '♫', 'now-playing-placeholder-art'));
      const labels = node('div', null, 'now-playing-labels');
      labels.append(node('div', now.song.title || 'Untitled song', 'now-playing-title'),
        node('div', now.artist?.preferred_display_alias || 'Artist needs review', 'now-playing-artist'));
      body.append(labels); $('now-playing').replaceChildren(body);
      $('edit-selected').hidden = false;
      const details = node('div', null, 'current-song-details');
      details.append(node('div', now.song.title || 'Untitled song', 'song-details-title'),
        node('div', now.artist?.preferred_display_alias || 'Artist needs review', 'detail-line'),
        node('h3', 'Style'), node('div', now.song.style || 'No style information available', 'detail-line'),
        node('h3', 'Playback history'), statsNode(now.statistics));
      if (now.artwork_url) {
        const image = node('img', null, 'song-details-artwork'); image.src = now.artwork_url;
        image.alt = 'Artwork for ' + (now.song.title || 'the current song'); details.prepend(image);
      }
      $('song-preview').replaceChildren(details);
    } else {
      nowPlayingId = null; $('edit-selected').hidden = true;
      const body = node('div', null, 'idle-message');
      const text = node('div');
      text.append(node('strong', 'A quiet moment.'), node('p', 'Nothing is playing right now. Your music and station preferences are ready to review.'));
      body.append(node('span', '♫', 'idle-icon'), text); $('now-playing').replaceChildren(body);
      $('song-preview').replaceChildren(node('p', 'Song details will appear here when a song is playing.', 'quiet'));
    }
  }
}
export async function refresh() {
  if (polling) return;
  polling = true;
  try {
    state = await api('/api/status');
    const scroll = $('conversation');
    const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 100;
    const changed = renderMessages(state.messages, !!state.station?.onboarding.complete, refresh);
    renderPolicies(state.station?.policies || [], !!state.busy, policyAction, discussPolicy);
    $('connection-label').textContent = state.error || (state.authenticated ? 'Your host is connected' : state.login?.pending ? 'Finish signing in, then return here.' : 'Connect ChatGPT to talk with your host');
    $('reconnect').hidden = state.authenticated && !state.error;
    $('reconnect').disabled = !!state.busy || connecting;
    $('signin').hidden = !state.login?.auth_url;
    if (state.login?.auth_url) $('signin').href = state.login.auth_url;
    renderContext(state.station); busyState();
    if (pendingQueueSong && editor.selected?.song.suno_song_id === pendingQueueSong.id) {
      const program = state.station?.programming;
      const present = program?.queue?.some(track => track.suno_song_id === pendingQueueSong.id);
      if (present) {
        queueFeedback = {id: pendingQueueSong.id, text: pendingQueueSong.position === 'next'
          ? 'Added to play next.' : 'Added to the end of the queue.'};
        pendingQueueSong = null;
      } else if (!state.busy) {
        queueFeedback = {id: pendingQueueSong.id, text: 'Your host could not add this song. Try again or ask your host in chat.'};
        pendingQueueSong = null;
      }
      if (queueFeedback?.id === editor.selected?.song.suno_song_id) $('queue-song-status').textContent = queueFeedback.text;
    }
    if (changed && nearBottom) scrollConversation();
  } catch (error) {
    $('connection-label').textContent = 'The application is unavailable. Check that it is still running.';
    $('send').disabled = true;
    showError(error);
  } finally { polling = false; }
}
async function connect() {
  const tab = window.open('about:blank', '_blank');
  if (tab) tab.opener = null;
  connecting = true; $('error').textContent = ''; $('step-action').disabled = true;
  try {
    const data = await api('/api/connect', {});
    if (data.auth_url) {
      $('signin').href = data.auth_url; $('signin').hidden = false;
      if (tab) tab.location = data.auth_url;
    } else if (tab) tab.close();
  } catch (error) { if (tab) tab.close(); showError(error); }
  finally { connecting = false; await refresh(); }
}
async function policyAction(action, policyId, revision) {
  $('error').textContent = '';
  try {
    const result = await api('/api/policies/action', {action, policy_id: policyId, revision});
    notify(result.message); await refresh();
  } catch (error) { showError(error); }
}
function discussPolicy(policy) {
  $('policies-dialog').close();
  $('message').value = 'I’d like to discuss or revise policy ' + policy.id + ', “' + policy.latest_revision.title + '”. ';
  $('message').focus();
}
function discussNewPolicy() {
  $('policies-dialog').close();
  $('message').value = 'I’d like to discuss adding a station policy. Help me decide whether this should become a lasting default.';
  $('message').focus();
}
$('reconnect').addEventListener('click', connect);
for (const action of ['start', 'stop']) $('' + action + '-programming').addEventListener('click', async () => {
  if (programmingAction) return;
  programmingAction = true; $('start-programming').disabled = true; $('stop-programming').disabled = true;
  try {
    await api('/api/programming/' + action, {});
    notify(action === 'start' ? 'Your host is preparing the radio program.' : 'The current song will finish, then your host will sign off.');
  } catch (error) { showError(error); }
  finally { programmingAction = false; await refresh(); }
});
$('step-action').addEventListener('click', async () => {
  try {
    const step = state?.station?.onboarding.current || 'connection';
    if (step === 'connection') return connect();
    if (step === 'music') return openDialog('import-dialog');
    if (step === 'catalog') return await editor.open();
    if (step === 'policies') {
      $('message').value = 'Let’s review our music catalog and discuss which normal operating preferences are worth establishing.';
      $('message').focus(); return;
    }
    await api('/api/onboarding/complete', {}); notify('Your station setup is complete.'); await refresh();
  } catch (error) { showError(error); }
});
document.querySelectorAll('#open-import, #station-import').forEach(button =>
  button.addEventListener('click', () => openDialog('import-dialog')));
$('browse-catalog').addEventListener('click', () => editor.open().catch(showError));
$('edit-selected').addEventListener('click', () => {
  if (nowPlayingId && state?.station?.now_playing) {
    editor.selected = state.station.now_playing;
    editor.open().catch(showError);
  }
});
async function queueCatalogSong(position) {
  if (queueAction || !editor.selected) return;
  const songId = editor.selected.song.suno_song_id;
  queueFeedback = null; queueAction = true; busyState(); $('error').textContent = '';
  try {
    await api('/api/programming/queue-song', {
      suno_song_id: songId,
      position
    });
    pendingQueueSong = {id: songId, position};
    queueFeedback = {id: songId, text: 'Request sent. Your host is preparing the song for the live queue.'};
    notify(position === 'next' ? 'Your host is preparing this song to play next.' : 'Your host is preparing this song for the end of the queue.');
  } catch (error) {
    pendingQueueSong = null;
    queueFeedback = {id: songId, text: error.message};
    showError(error);
  } finally { queueAction = false; await refresh(); }
}
$('queue-song-next').addEventListener('click', () => queueCatalogSong('next'));
$('queue-song-append').addEventListener('click', () => queueCatalogSong('append'));
$('open-policies').addEventListener('click', () => openDialog('policies-dialog'));
$('step-policy-review').addEventListener('click', () => openDialog('policies-dialog'));
$('add-policy').addEventListener('click', discussNewPolicy);
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
$('composer').addEventListener('submit', async event => {
  event.preventDefault(); if (sending || state?.busy) return;
  sending = true; $('error').textContent = ''; busyState();
  try { await api('/api/messages', {text: $('message').value}); $('message').value = ''; }
  catch (error) { showError(error); }
  finally { sending = false; await refresh(); scrollConversation(); }
});
$('message').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('composer').requestSubmit(); }
});
$('import-form').addEventListener('submit', async event => {
  event.preventDefault(); if (importing) return;
  importing = true; $('import-button').disabled = true; $('import-status').textContent = 'Importing… Please leave this page open.'; busyState();
  try {
    const result = await api('/api/import', {text: $('discord-text').value});
    renderImport(result); $('import-dialog').close(); $('import-status').textContent = '';
    notify('Import finished. Review the results in your conversation.'); await refresh(); scrollConversation();
  } catch (error) { $('import-status').textContent = error.message; }
  finally { importing = false; $('import-button').disabled = false; busyState(); }
});
refresh(); setInterval(refresh, 1800);
