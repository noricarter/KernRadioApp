import {$, node} from './ui.js';
let messagesKey = null, policiesKey = null;
const dismissedStorageKey = 'kern-radio-dismissed-chat-messages';
let dismissedMessages = new Set();
try { dismissedMessages = new Set(JSON.parse(localStorage.getItem(dismissedStorageKey) || '[]')); } catch {}
function richText(element, text) {
  for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
    if (part.startsWith('**') && part.endsWith('**')) element.append(node('strong', part.slice(2, -2)));
    else if (part.startsWith('`') && part.endsWith('`')) element.append(node('code', part.slice(1, -1)));
    else element.append(document.createTextNode(part));
  }
}
export function renderMessages(messages, ready = false, onDismiss = () => {}) {
  const key = JSON.stringify([messages, ready]);
  if (key === messagesKey) return false;
  messagesKey = key;
  const elements = [];
  if (!messages.length) {
    const welcome = node('div', null, 'welcome-card');
    welcome.append(node('h2', ready ? 'Welcome back, Kern.' : 'Welcome, Kern.'),
      node('p', ready ? 'Your station preferences are saved.' : 'Your radio starts with a conversation.'),
      node('p', ready ? 'Keep talking with your host, explore your music, or open Policies beside Now Playing to manage station preferences.' : 'Connect your host, bring in the community’s music, and shape the station together. The guide on the right will help you along.'));
    elements.push(welcome);
  }
  for (const message of messages) {
    if (message.id && dismissedMessages.has(message.id)) continue;
    const row = node('div', null, 'message-row ' + message.role);
    if (message.role === 'agent') {
      const avatar = node('img', null, 'avatar'); avatar.src = '/assets/mascot.png'; avatar.alt = 'Radio host';
      row.append(avatar);
    }
    const bubble = node('div', null, 'message-bubble');
    richText(bubble, message.text);
    row.append(bubble); elements.push(row);
    if (message.role !== 'user' && message.id) {
      const dismiss = node('button', '×', 'dismiss-message');
      dismiss.type = 'button'; dismiss.title = 'Dismiss this message';
      dismiss.setAttribute('aria-label', 'Dismiss this message');
      dismiss.addEventListener('click', () => {
        dismissedMessages.add(message.id);
        try { localStorage.setItem(dismissedStorageKey, JSON.stringify([...dismissedMessages].slice(-250))); } catch {}
        row.remove(); messagesKey = null; onDismiss();
      });
      row.append(dismiss);
    }
  }
  $('messages').replaceChildren(...elements);
  return true;
}
export function renderPolicies(policies, busy, onAction, onDiscuss) {
  const key = JSON.stringify([policies, busy]);
  if (key === policiesKey) return false;
  policiesKey = key;
  const cards = [];
  for (const policy of policies) {
    const revision = policy.latest_revision;
    const proposed = revision.approval_status === 'proposed' && !revision.declined;
    const approved = revision.approval_status === 'approved';
    const label = revision.declined ? 'Declined' : proposed ? 'Awaiting approval' : policy.active ? 'Approved · Active' : 'Disabled';
    const card = node('article', null, 'policy-card');
    card.dataset.policyId = policy.id;
    const top = node('div', null, 'card-top');
    top.append(node('span', proposed ? '▤  Proposed Station Policy' : '▤  Station Policy'),
      node('span', label, 'policy-badge ' + (proposed ? '' : approved && policy.active ? 'approved' : 'declined')));
    card.append(top, node('h3', revision.title), node('p', revision.instruction));
    if (revision.approval_status === 'proposed' && policy.active) card.append(node('p', revision.declined ? 'The earlier approved revision remains active.' : 'The earlier approved revision remains active until you approve this replacement.', 'policy-reason'));
    const rationale = node('details');
    rationale.append(node('summary', 'Why this was proposed'), node('p', revision.rationale), node('p', revision.basis));
    card.append(rationale);
    const actions = node('div', null, 'card-actions');
    if (proposed) {
      const approve = node('button', '✓  Approve', 'button-green'); approve.dataset.action = 'approve';
      approve.disabled = busy; approve.addEventListener('click', () => onAction('approve', policy.id, revision.revision));
      actions.append(approve);
    }
    const discuss = node('button', '✎  Discuss / Edit'); discuss.dataset.action = 'discuss';
    discuss.addEventListener('click', () => onDiscuss(policy));
    actions.append(discuss);
    if (proposed || policy.active) {
      const action = proposed ? 'decline' : 'disable';
      const decline = node('button', proposed ? '×  Decline' : 'Disable');
      decline.dataset.action = action; decline.disabled = busy;
      decline.addEventListener('click', () => onAction(action, policy.id, revision.revision));
      actions.append(decline);
    }
    card.append(actions); cards.push(card);
  }
  if (!cards.length) cards.push(node('p', 'No station policies yet. Start a conversation with your host to suggest a default.', 'quiet'));
  $('policy-list').replaceChildren(...cards);
  return true;
}
export function renderImport(result) {
  const card = node('article', null, 'result-card');
  card.dataset.batchId = result.batch_id;
  card.append(node('div', '♬  Community Music Import · Batch ' + result.batch_id, 'card-top'),
    node('h3', result.counts.failed ? 'Import finished with entries needing attention' : 'Your import is ready'));
  card.append(node('p', result.counts.added + ' imported · ' + result.counts.existing + ' already in catalog · ' + result.counts.failed + ' unresolved', 'result-summary'));
  const list = node('ul');
  for (const entry of result.entries) {
    const item = node('li', (entry.outcome === 'failed' ? 'Needs attention: ' : entry.outcome === 'existing' ? 'Already in catalog: ' : 'Imported: ') + (entry.title || entry.suno_song_id || (entry.youtube_video_id ? 'YouTube · ' + entry.youtube_video_id : 'Unrecognized text')));
    for (const warning of [entry.error, ...(entry.warnings || [])]) if (warning) item.append(node('p', warning));
    if (entry.missing_metadata?.length) {
      const fields = {title:'title', lyrics:'lyrics', style:'style', suno_username:'Suno creator'};
      item.append(node('p', 'Unavailable from Suno: ' + entry.missing_metadata.map(value => fields[value]).join(', ')));
    }
    if (entry.source_text && entry.outcome === 'failed') {
      const detail = node('details'); detail.append(node('summary', 'Original entry'), node('p', entry.source_text)); item.append(detail);
    }
    list.append(item);
  }
  card.append(list);
  $('import-card').replaceChildren(card);
}
export function scrollConversation() {
  $('conversation').scrollTop = $('conversation').scrollHeight;
}
