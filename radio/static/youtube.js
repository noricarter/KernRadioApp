import {$, api, node, notify} from './ui.js';

export class YouTubeCatalog {
  constructor(onSelected) {
    this.items = []; this.onSelected = onSelected;
    $('youtube-more').addEventListener('click', () => this.load(true).catch(error => notify(error.message)));
  }
  async load(more = false) {
    const data = await api('/api/youtube?offset=' + (more ? this.items.length : 0));
    this.items = more ? [...this.items, ...data.items] : data.items;
    $('youtube-catalog').hidden = !data.total;
    $('youtube-count').textContent = 'YouTube submissions · ' + data.total;
    $('youtube-more').hidden = this.items.length >= data.total;
    $('youtube-list').replaceChildren(...this.items.map(track => {
      const button = node('button', track.title || 'YouTube video ' + track.video_id, 'catalog-item'); button.type = 'button';
      button.addEventListener('click', () => this.select(track.video_id).catch(error => notify(error.message)));
      return button;
    }));
  }
  async select(id) {
    const data = await api('/api/youtube/' + encodeURIComponent(id));
    this.onSelected(); $('song-form').hidden = true; $('save-song').hidden = true; $('editor-empty').hidden = true;
    const detail = $('import-entry-detail'); detail.hidden = false; detail.setAttribute('aria-label', 'Submission details');
    detail.replaceChildren(node('h3', data.track.title || 'YouTube submission'), node('p', 'YouTube · Details supplied with the post'));
    const link = node('a', 'Open on YouTube'); link.href = 'https://www.youtube.com/watch?v=' + encodeURIComponent(id); link.target = '_blank'; link.rel = 'noopener noreferrer';
    detail.append(link);
    if (data.artist?.preferred_display_alias) detail.append(node('p', 'Submitted by ' + data.artist.preferred_display_alias));
    for (const [key, label] of [['genre','Genre'],['description','About'],['content_warning','Content warning']]) {
      if (data.track[key]) detail.append(node('h4', label), node('p', data.track[key]));
    }
    for (const post of data.submissions) {
      const original = node('details'); original.append(node('summary', 'Original post' + (post.discord_handle ? ' · ' + post.discord_handle : '')), node('pre', post.original_message, 'review-source')); detail.append(original);
    }
    detail.parentElement.scrollTop = 0;
  }
}
