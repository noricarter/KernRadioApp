import {$, api, node, notify, openDialog} from './ui.js';

export class ImportReview {
  constructor(onSelected) {
    this.items = []; this.batchId = null; this.offset = 0; this.onSelected = onSelected;
    $('import-review-batch').addEventListener('change', () => this.load(Number($('import-review-batch').value)).then(() => this.selectFirst()).catch(error => notify(error.message)));
    $('import-review-prev').addEventListener('click', () => this.load(this.batchId, this.offset - 20).catch(error => notify(error.message)));
    $('import-review-next').addEventListener('click', () => this.load(this.batchId, this.offset + 20).catch(error => notify(error.message)));
  }
  async load(batchId = this.batchId, offset = 0) {
    const data = await api('/api/import-review?offset=' + Math.max(0, offset) + (batchId ? '&batch_id=' + batchId : ''));
    if (!data.total && batchId && data.batches.length) return this.load(null, 0);
    this.items = data.items; this.offset = data.offset; this.batchId = data.batch?.id || null;
    $('import-review').hidden = !data.total;
    $('catalog-dialog').classList.toggle('has-import-issues', !!data.total);
    $('catalog-review-note').textContent = data.total ? data.total + ' unresolved import results in batch ' + this.batchId + ' need attention. These are separate from the imported songs below.' : 'Review songs and artist names. Changes are saved to your local catalog.';
    $('import-review-heading').textContent = '⚠ ' + data.total + ' need attention';
    $('import-review-batch').replaceChildren(...data.batches.map(batch => {
      const option = node('option', 'Batch ' + batch.id + ' · ' + batch.unresolved_count + ' unresolved');
      option.value = batch.id; return option;
    }));
    $('import-review-batch').value = this.batchId || '';
    $('import-review-list').replaceChildren(...data.items.map((item, index) => {
      const button = node('button', null, 'catalog-item import-issue');
      button.type = 'button'; button.dataset.itemId = item.id;
      button.append(node('strong', '⚠ Result ' + (data.offset + index + 1) + ' · #' + item.id), node('span', item.error));
      button.addEventListener('click', () => this.select(item.id).catch(error => notify(error.message)));
      return button;
    }));
    $('import-review-prev').disabled = this.offset === 0;
    $('import-review-next').disabled = this.offset + this.items.length >= data.total;
    $('import-review-page').textContent = data.total ? (this.offset + 1) + '–' + (this.offset + this.items.length) + ' of ' + data.total : '';
  }
  async selectFirst() { if (this.items.length) await this.select(this.items[0].id); }
  async select(id) {
    const entry = await api('/api/import-review/' + id);
    this.onSelected();
    $('song-form').hidden = true; $('save-song').hidden = true; $('editor-empty').hidden = true;
    const detail = $('import-entry-detail'); detail.hidden = false;
    const recognized = !!entry.review_state?.parsed_songs;
    detail.replaceChildren(node('h3', '⚠ Unresolved result #' + entry.id), node('p', 'Previous import: ' + entry.error, 'review-notice'));
    if (!recognized) detail.append(node('p', entry.explanation));
    if (entry.review_state?.parsed_songs) {
      const state = entry.review_state;
      detail.append(node('p', 'Recognized ' + state.parsed_songs + ' song posts; ' + state.catalog_songs + ' already in your catalog.' + (state.unrecognized_posts ? ' ' + state.unrecognized_posts + ' other posts still need attention.' : ' You can import this group together now.'), 'review-notice'));
      for (const reason of state.remaining_reasons || []) detail.append(node('p', reason, 'review-notice'));
    }
    const fields = node('ul', null, 'questioned-fields');
    for (const field of recognized ? [] : entry.questioned_fields) {
      const item = node('li'); item.append(node('strong', field.label + ': '), document.createTextNode(field.reason)); fields.append(item);
    }
    detail.append(fields, node('h4', recognized ? 'Original copied posts' : 'Original copied text · Highlighted fields need review'));
    const source = node('pre', null, 'review-source');
    for (const part of entry.source_parts) {
      if (part.questioned && !recognized) { const mark = node('mark', part.text); mark.title = part.label + ' needs review'; source.append(mark); }
      else source.append(document.createTextNode(part.text));
    }
    const correct = node('button', 'Prepare corrected import'); correct.type = 'button';
    correct.addEventListener('click', () => {
      $('discord-text').value = entry.source_text || '';
      $('catalog-dialog').close(); openDialog('import-dialog');
      $('import-status').textContent = 'Recognized posts are separated automatically. Check any remaining issues before importing. Saved history is kept; groups whose songs are all in the catalog leave the review list.';
    });
    detail.append(source, correct, node('p', 'The original failure is kept in history. This group leaves the review list when every post is recognized and its songs are in the catalog.', 'field-note'));
    document.querySelectorAll('.catalog-item').forEach(button => button.classList.toggle('selected', Number(button.dataset.itemId) === id));
    detail.parentElement.scrollTop = 0;
  }
}
