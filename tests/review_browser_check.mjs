// Run only against tests.ui_server with an isolated copy of the station catalog.
// Exercises real review UI and makes one test-only metadata edit in that copy.
import {browser} from './browser_driver.mjs';
import {writeFile} from 'node:fs/promises';

const b = await browser(process.argv[2], 1366, 900);
const report = {scope:'Actual Chrome/Flask with isolated copy of real catalog and cached originals', renders:[]};
try {
  await b.waitFor('!document.getElementById("send").disabled && document.getElementById("step-action").textContent === "Review music"');
  await b.evaluate('document.getElementById("step-action").click()');
  await b.waitFor('document.querySelectorAll(".import-issue").length === 19 && document.querySelector(".review-source mark")');
  report.failuresShown = await b.evaluate('document.querySelectorAll(".import-issue").length');
  report.highlightedOriginalFields = await b.evaluate('document.querySelectorAll(".review-source mark").length');
  const originalMatches = await b.evaluate(`(async () => {
    const id=document.querySelector('.import-issue.selected').dataset.itemId;
    const entry=await (await fetch('/api/import-review/'+id)).json();
    return document.querySelector('.review-source').textContent === entry.source_text;
  })()`);
  if (!originalMatches) throw Error('Original text changed during highlighted rendering');
  report.exactOriginalUnicode = originalMatches;
  for (const [width,height] of [[1366,900],[1024,768],[1680,941]]) {
    await b.resize(width,height);
    await b.screenshot('docs/screenshots/review-unresolved-' + width + '.png');
    const render=await b.evaluate('({width:innerWidth,height:innerHeight,documentOverflow:document.documentElement.scrollWidth>innerWidth,dialogOverflow:document.getElementById("catalog-dialog").scrollWidth>document.getElementById("catalog-dialog").clientWidth,reviewButtonVisible:document.getElementById("review-catalog").getBoundingClientRect().bottom < innerHeight})');
    if(render.documentOverflow || render.dialogOverflow || !render.reviewButtonVisible) throw Error(JSON.stringify(render));
    report.renders.push(render);
  }
  const target = await b.evaluate(`(async () => {
    for(let offset=0;offset<100;offset+=20) {
      const page=await (await fetch('/api/catalog?offset='+offset)).json();
      const song=page.items.find(song=>song.field_issues.artist_id || song.field_issues.preferred_display_alias);
      if(song) return {offset,id:song.suno_song_id};
      if(offset+page.items.length>=page.total) break;
    }
    throw Error('Expected actual unresolved artist in isolated catalog');
  })()`);
  for(let offset=0;offset<target.offset;offset+=20) {
    await b.evaluate('document.getElementById("catalog-next").click()');
    await b.waitFor('document.getElementById("catalog-page").textContent.startsWith(' + JSON.stringify(String(offset+21)+'–') + ')');
  }
  await b.evaluate('document.querySelector(' + JSON.stringify('[data-song-id="'+target.id+'"]') + ').click()');
  await b.waitFor('!document.getElementById("song-form").hidden && document.getElementById("song-artwork").naturalWidth > 0');
  report.artwork = await b.evaluate('({width:document.getElementById("song-artwork").naturalWidth,height:document.getElementById("song-artwork").naturalHeight,source:document.getElementById("song-artwork").getAttribute("src")})');
  report.markedFields=await b.evaluate('[...document.querySelectorAll(".field-needs-review")].map(element=>element.id)');
  if(!report.markedFields.includes('artist-name')) throw Error('Missing preferred-name highlight');
  await b.resize(1366,900);
  await b.screenshot('docs/screenshots/review-fields-artwork-1366.png');
  await b.evaluate(`document.getElementById('song-title').value='Browser verification · Edited title';
    document.getElementById('artist-select').value=''; document.getElementById('artist-select').dispatchEvent(new Event('change'));
    document.getElementById('artist-name').value='Browser review fixture 🌲';
    document.getElementById('save-song').click();`);
  await b.waitFor('document.getElementById("save-status").textContent === "Saved to your catalog."');
  if(await b.evaluate('document.getElementById("artist-name").classList.contains("field-needs-review")')) throw Error('Resolved field remains marked');
  report.metadataEdit = await b.evaluate(`(async () => {
    const detail=await (await fetch('/api/catalog/'+${JSON.stringify(target.id)})).json();
    return {title:detail.song.title,preferredName:detail.artist.preferred_display_alias,fieldIssues:detail.field_issues};
  })()`);
  await b.call('Page.reload');
  await b.waitFor('document.readyState === "complete" && document.getElementById("step-action").textContent === "Review music"');
  await b.evaluate('document.getElementById("step-action").click()');
  await b.waitFor('document.querySelectorAll(".import-issue").length === 19');
  report.failuresPreservedAfterEditAndRefresh=true;
  report.programmingDisabled=await b.evaluate('document.getElementById("start-programming").disabled && document.getElementById("stop-programming").disabled');
  report.browserErrors=b.errors;
  if(b.errors.length) throw Error(JSON.stringify(b.errors));
  await writeFile('docs/review-artwork-browser-result.json',JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
} finally { await b.close(); }
