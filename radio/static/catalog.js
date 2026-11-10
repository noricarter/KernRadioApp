import {$, api, node, notify, openDialog, statsNode} from './ui.js';
import {ImportReview} from './review.js';
import {YouTubeCatalog} from './youtube.js';

export class CatalogEditor {
  constructor(onSaved) {
    this.onSaved = onSaved; this.selected = null; this.artists = []; this.offset = 0; this.query = ''; this.searchTimer = null; this.pageRequest = 0;
    this.review = new ImportReview(() => { this.selected = null; });
    this.youtube = new YouTubeCatalog(() => { this.selected = null; });
    $('catalog-prev').addEventListener('click', () => this.loadPage(this.offset - 20).catch(notifyError));
    $('catalog-next').addEventListener('click', () => this.loadPage(this.offset + 20).catch(notifyError));
    $('catalog-search').addEventListener('input', () => {
      clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => {
        this.query = $('catalog-search').value.trim();
        this.loadPage(0).catch(notifyError);
      }, 250);
    });
    $('artist-select').addEventListener('change', () => this.showArtist());
    $('artist-name').addEventListener('input', () => this.showArtistMatch());
    $('use-existing-artist').addEventListener('click', () => {
      const artist = this.matchingArtist();
      if (!artist) return;
      $('artist-select').value = artist.id;
      this.showArtist();
      $('save-status').textContent = 'Existing artist selected. Save changes to update this song.';
    });
    $('more-artists').addEventListener('click', () => this.loadArtists(true).catch(notifyError));
    $('song-form').addEventListener('submit', event => this.save(event));
    $('review-catalog').addEventListener('click', async () => {
      try { await api('/api/catalog-review', {}); $('catalog-dialog').close(); notify('Catalog review saved.'); await onSaved(); }
      catch (error) { notifyError(error); }
    });
  }
  async open() {
    openDialog('catalog-dialog');
    await this.review.load();
    await Promise.all([this.loadArtists(), this.loadPage(this.offset), this.youtube.load()]);
    if (this.selected) await this.select(this.selected.song.suno_song_id);
    else await this.review.selectFirst();
  }
  async loadArtists(more = false) {
    const batch = await api('/api/artists?offset=' + (more ? this.artists.length : 0));
    this.artists = more ? [...this.artists, ...batch] : batch;
    if (this.selected?.artist && !this.artists.some(a => a.id === this.selected.artist.id)) this.artists.push(this.selected.artist);
    $('more-artists').hidden = batch.length < 100;
    this.artistOptions(this.selected?.song.artist_id);
  }
  artistOptions(id) {
    const options = this.artists.map(artist => {
      const option = node('option', artist.preferred_display_alias || artist.suno_artist_name || artist.discord_handle || 'Unnamed artist');
      option.value = artist.id; return option;
    });
    const empty = node('option', 'Unassigned / create an artist'); empty.value = '';
    $('artist-select').replaceChildren(empty, ...options);
    $('artist-select').value = id || '';
  }
  showArtist() {
    const artist = this.artists.find(value => value.id === Number($('artist-select').value));
    $('artist-name').value = artist?.preferred_display_alias || '';
    $('artist-aliases').value = artist?.aliases.join('\n') || '';
    this.showArtistMatch();
  }
  matchingArtist() {
    const name = $('artist-name').value;
    const selectedId = Number($('artist-select').value);
    return name && this.artists.find(artist => artist.id !== selectedId &&
      (artist.preferred_display_alias === name || artist.aliases.includes(name)));
  }
  showArtistMatch() {
    const artist = this.matchingArtist();
    $('existing-artist-match').hidden = !artist;
    if (artist) {
      const name = artist.preferred_display_alias || $('artist-name').value;
      $('existing-artist-note').textContent = '“' + name + '” already exists. Use that artist for this song while keeping their saved name and aliases.';
      $('use-existing-artist').textContent = 'Use existing artist: ' + name;
    }
  }
  async loadPage(offset = 0) {
    this.offset = Math.max(0, offset);
    const request = ++this.pageRequest;
    const data = await api('/api/catalog?offset=' + this.offset + '&q=' + encodeURIComponent(this.query));
    if (request !== this.pageRequest) return;
    $('catalog-list').replaceChildren(...data.items.map(song => {
      const button = node('button', null, 'catalog-item' + (this.selected?.song.suno_song_id === song.suno_song_id ? ' selected' : ''));
      button.type = 'button'; button.dataset.songId = song.suno_song_id;
      if (song.artwork_url) {
        const image = node('img', null, 'catalog-artwork'); image.src = song.artwork_url; image.alt = ''; image.loading = 'lazy'; button.append(image);
      }
      button.append(node('strong', song.title || 'Untitled song'),
        node('span', song.artist_name || 'Artist needs review', song.artist_name ? '' : 'unresolved'));
      if (Object.keys(song.field_issues || {}).length) {
        button.classList.add('song-needs-review'); button.append(node('span', '⚠ Review highlighted fields', 'review-label'));
      }
      button.addEventListener('click', () => this.select(song.suno_song_id).catch(notifyError));
      return button;
    }));
    if (!data.items.length) $('catalog-list').append(node('p', this.query ? 'No songs or artists match that search.' : 'Your imported songs will appear here.', 'quiet'));
    $('catalog-prev').disabled = this.offset === 0;
    $('catalog-next').disabled = this.offset + data.items.length >= data.total;
    $('catalog-page').textContent = data.total ? (this.offset + 1) + '–' + (this.offset + data.items.length) + ' of ' + data.total : this.query ? 'No matches' : 'No songs yet';
    if (!this.selected && !this.review.items.length && data.items.length) await this.select(data.items[0].suno_song_id);
  }
  async select(id) {
    this.selected = await api('/api/catalog/' + encodeURIComponent(id));
    const {song} = this.selected;
    $('import-entry-detail').hidden = true;
    $('editor-empty').hidden = true; $('song-form').hidden = false;
    $('save-song').hidden = false;
    $('editor-heading').textContent = song.title || 'Song details';
    $('song-title').value = song.title || '';
    $('song-artwork').hidden = !this.selected.artwork_url;
    $('song-artwork-note').hidden = !this.selected.artwork_url;
    $('song-artwork-placeholder').hidden = !!this.selected.artwork_url;
    if (this.selected.artwork_url) $('song-artwork').src = this.selected.artwork_url;
    else $('song-artwork').removeAttribute('src');
    $('song-style').value = song.style || '';
    $('song-lyrics').value = song.lyrics || '';
    $('suno-username').value = song.suno_username || '';
    if (this.selected.artist) {
      this.artists = this.artists.filter(artist => artist.id !== this.selected.artist.id);
      this.artists.push(this.selected.artist);
    }
    this.artistOptions(song.artist_id); this.showArtist();
    this.highlightFields(this.selected.field_issues || {});
    $('save-status').textContent = '';
    $('editor-stats').replaceChildren(node('h4', 'Playback statistics · Read only'), statsNode(this.selected.statistics),
      node('p', 'Calculated from recorded playback events. These values are not editable.'));
    document.querySelectorAll('.catalog-item').forEach(button => button.classList.toggle('selected', button.dataset.songId === id));
  }
  highlightFields(issues) {
    const fields = {title:'song-title', artist_id:'artist-select', preferred_display_alias:'artist-name', style:'song-style', lyrics:'song-lyrics', suno_username:'suno-username'};
    document.querySelectorAll('.field-review-message').forEach(element => element.remove());
    for (const [field, id] of Object.entries(fields)) {
      const element = $(id); element.classList.toggle('field-needs-review', !!issues[field]);
      element.removeAttribute('aria-describedby');
      if (issues[field]) {
        const note = node('p', '⚠ ' + issues[field], 'field-review-message'); note.id = id + '-review';
        element.after(note); element.setAttribute('aria-describedby', note.id);
      }
    }
    $('song-field-review-note').hidden = !Object.keys(issues).length;
    $('song-lyrics').closest('details').open = !!(issues.lyrics || issues.suno_username);
  }
  async save(event) {
    event.preventDefault();
    if (!this.selected) return;
    if (this.matchingArtist()) {
      this.showArtistMatch();
      $('save-status').textContent = 'This name belongs to an existing artist. Choose “Use existing artist” above, then save.';
      $('use-existing-artist').focus();
      return;
    }
    const button = event.submitter || $('save-song'); button.disabled = true; $('save-status').textContent = 'Saving…';
    try {
      const payload = {metadata: {title: $('song-title').value || null, style: $('song-style').value || null,
        lyrics: $('song-lyrics').value || null, suno_username: $('suno-username').value || null},
        artist_id: $('artist-select').value ? Number($('artist-select').value) : null,
        artist: {aliases: $('artist-aliases').value.split('\n').filter(value => value.trim())}};
      if ($('artist-name').value) payload.artist.preferred_display_alias = $('artist-name').value;
      const id = this.selected.song.suno_song_id;
      this.selected = await api('/api/catalog/' + encodeURIComponent(id), payload);
      await this.loadArtists(); await this.select(id); await this.loadPage(this.offset);
      $('save-status').textContent = 'Saved to your catalog.'; notify('Song details saved.');
      await this.onSaved();
    } catch (error) { $('save-status').textContent = error.message; notifyError(error); }
    finally { button.disabled = false; }
  }
}
function notifyError(error) { notify(error.message); }
