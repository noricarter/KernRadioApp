export const $ = id => document.getElementById(id);
export const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = text;
  if (className) element.className = className;
  return element;
};
const token = document.querySelector('meta[name="app-token"]').content;
export async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-App-Token': token},
    body: JSON.stringify(body)
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error('The application could not finish this request. Please try again.'); }
  if (!response.ok) throw new Error(data.error || 'Please try again.');
  return data;
}
let timer;
export function notify(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(timer); timer = setTimeout(() => { $('toast').hidden = true; }, 5500);
}
export function showError(error) { $('error').textContent = error.message || String(error); }
export function openDialog(id) { if (!$(id).open) $(id).showModal(); }
export function statsNode(statistics) {
  const grid = node('div', null, 'stats-grid');
  const values = [
    ['Completed plays', statistics.completed_plays],
    ['Recorded airtime', formatAirtime(statistics.airtime_seconds)],
    ['Last played', statistics.last_played_at ? new Date(statistics.last_played_at).toLocaleString([], {dateStyle:'medium',timeStyle:'short'}) : 'Never'],
    ['Interrupted plays', statistics.interrupted_plays]
  ];
  for (const [label, value] of values) {
    const item = node('div', null, 'stat');
    item.append(node('span', label), node('strong', String(value))); grid.append(item);
  }
  return grid;
}
function formatAirtime(value) {
  const seconds = Math.floor(value || 0);
  return seconds >= 3600 ? Math.floor(seconds / 3600) + 'h ' + Math.floor(seconds % 3600 / 60) + 'm'
    : Math.floor(seconds / 60) + 'm ' + seconds % 60 + 's';
}
