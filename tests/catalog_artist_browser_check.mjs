// Run only against an isolated Flask fixture with the two artists below.
import {browser} from './browser_driver.mjs';

const b = await browser(process.argv[2], 1024, 768);
const id = 'artist-correction-test';
const check = async (label, expression) => {
  if (!await b.evaluate(expression)) throw new Error(label);
  console.log('PASS: ' + label);
};
try {
  await b.evaluate(`document.getElementById('browse-catalog').click()`);
  await b.waitFor(`document.getElementById('catalog-dialog').open && !document.getElementById('song-form').hidden`);
  await b.evaluate(`document.getElementById('artist-name').value='Seraphym'; document.getElementById('artist-name').dispatchEvent(new Event('input'));`);
  await check('Existing artist offers an explicit association action', `!document.getElementById('existing-artist-match').hidden`);
  await b.evaluate(`document.getElementById('save-song').click()`);
  await check('Conflicting rename is explained beside Save', `document.getElementById('save-status').textContent.includes('Use existing artist') && !document.getElementById('save-status').closest('.catalog-editor')`);
  await check('Blocked rename does not change the song', `(async()=>{const detail=await(await fetch('/api/catalog/${id}')).json();return detail.artist.preferred_display_alias==='Seraphym, Candy Gremlin 2026'})()`);
  await b.evaluate(`document.getElementById('use-existing-artist').click()`);
  await check('Choosing existing artist keeps their aliases', `document.getElementById('artist-aliases').value.includes('_seraphym') && !document.getElementById('artist-aliases').value.includes('Candy Gremlin')`);
  await b.evaluate(`document.getElementById('save-song').click()`);
  await b.waitFor(`document.getElementById('save-status').textContent==='Saved to your catalog.'`);
  await check('Association and aliases are persisted through Flask', `(async()=>{const detail=await(await fetch('/api/catalog/${id}')).json();return detail.artist.preferred_display_alias==='Seraphym' && detail.artist.aliases.includes('_seraphym')})()`);
  for (const [width,height] of [[1024,768],[1366,900]]) {
    await b.resize(width,height);
    await check('Save status and action visible at '+width, `document.getElementById('save-status').getBoundingClientRect().top>=0 && document.getElementById('save-song').getBoundingClientRect().bottom<=innerHeight`);
  }
  await b.call('Page.reload');
  await b.waitFor(`document.getElementById('setup-steps').children.length===4`);
  await b.evaluate(`document.getElementById('browse-catalog').click()`);
  await b.waitFor(`document.getElementById('artist-name').value==='Seraphym'`);
  await check('Association survives browser refresh', `document.getElementById('artist-aliases').value.includes('_seraphym')`);
  await b.evaluate(`document.getElementById('artist-name').value='Seraphym 🌲';document.getElementById('save-song').click()`);
  await b.waitFor(`document.getElementById('save-status').textContent==='Saved to your catalog.'`);
  await b.evaluate(`document.querySelector('[data-song-id="${id}"]').click()`);
  await b.waitFor(`document.getElementById('artist-name').value==='Seraphym 🌲'`);
  await check('Reselecting a song uses the saved name', `document.getElementById('artist-aliases').value.includes('Seraphym 🌲')`);
  if (b.errors.length) throw new Error(JSON.stringify(b.errors));
} finally { await b.close(); }
