import {browser} from './browser_driver.mjs';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
const url=process.argv[2], mode=process.argv[3]||'workflow';
await mkdir('docs/screenshots',{recursive:true});
const b=await browser(url,1680,941);
const report={url,mode,checks:[],screenshots:[]};
const check=async (name,expression) => {
  const value=await b.evaluate(expression); if(!value) throw new Error('Failed: '+name);
  report.checks.push(name);
};
const shot=async name => { const path='docs/screenshots/'+name+'.png'; await b.screenshot(path); report.screenshots.push(path); };
try {
  await b.waitFor('document.getElementById("setup-steps").children.length===4');
  await check('Programming controls are disabled', 'document.getElementById("start-programming").disabled && document.getElementById("stop-programming").disabled');
  await check('Approved mascot loads', 'document.querySelector(".host-mascot").naturalWidth>0');
  if(mode==='workflow') {
    await b.waitFor('document.getElementById("step-title").textContent==="Import your music"');
    await check('Empty catalog uses onboarding, not empty statistics', '!document.getElementById("onboarding").hidden && document.getElementById("station-panels").hidden');
    await shot('onboarding-connected-1680');
    await b.evaluate('document.getElementById("open-import").click()');
    await b.waitFor('document.getElementById("import-dialog").open');
    await shot('import-dialog-1680');
    const text=await readFile('tests/fixtures/crucible_posts.txt','utf8');
    await b.evaluate('document.getElementById("discord-text").value='+JSON.stringify(text));
    await b.evaluate('document.getElementById("import-form").requestSubmit()');
    await b.waitFor('!document.getElementById("import-dialog").open && document.querySelector(".result-card")',120000);
    await check('Actual import reports four songs', 'document.querySelector(".result-summary").textContent.startsWith("4 imported")');
    await b.resize(1366,900); await shot('import-results-1366');
    await b.evaluate('document.getElementById("step-action").click()');
    await b.waitFor('document.getElementById("catalog-dialog").open && document.querySelectorAll(".catalog-item").length===4 && !document.getElementById("song-form").hidden');
    await b.evaluate('document.getElementById("song-title").value="UI verification — 風";document.getElementById("artist-name").value="Sickseven UI review";document.getElementById("artist-aliases").value="Sickseven [TCBL]\\nCommunity alias 🌲";document.getElementById("song-style").value="Reviewed: Psychedelic EDM"');
    await b.evaluate('document.getElementById("save-song").click()');
    await b.waitFor('document.getElementById("save-status").textContent==="Saved to your catalog."');
    await check('No editable calculated statistics', 'document.querySelectorAll("#editor-stats input,#editor-stats textarea").length===0');
    await shot('catalog-editor-1366');
    await b.evaluate('document.getElementById("review-catalog").click()');
    await b.waitFor('document.getElementById("step-title").textContent==="Shape your station together"');
    const request='For this isolated UI evaluation only, propose one default policy that ordinary programming should favor musical variety. Save it as a proposal for my review, not an approved policy. Do not start programming.';
    await b.evaluate('document.getElementById("message").value='+JSON.stringify(request)+';document.getElementById("composer").requestSubmit()');
    await b.evaluate('document.getElementById("open-policies").click()');
    await b.waitFor('document.querySelector("#policy-list .policy-card [data-action=approve]") && !document.querySelector("#policy-list .policy-card [data-action=approve]").disabled',220000);
    await check('Real agent proposal remains unapproved', 'document.querySelector("#policy-list .policy-badge").textContent==="Awaiting approval"');
    await shot('policy-proposal-1366');
    await b.evaluate('document.querySelector("#policy-list .policy-card [data-action=approve]").click()');
    await b.waitFor('document.getElementById("step-title").textContent==="Your station is ready"');
    await check('Policy approval stays out of chat', '!document.getElementById("messages").textContent.includes("Approved and active:")');
    await b.evaluate('document.querySelector("[data-close=policies-dialog]").click()');
    await b.evaluate('document.getElementById("step-action").click()');
    await b.waitFor('document.getElementById("onboarding").hidden && !document.getElementById("station-panels").hidden');
    await check('Station remains idle', 'document.getElementById("station-status").textContent==="Station: Idle"');
    await b.call('Page.reload');
    await b.waitFor('document.getElementById("onboarding").hidden && !document.getElementById("station-panels").hidden');
    await b.evaluate('document.getElementById("browse-catalog").click()');
    await b.waitFor('document.getElementById("song-title").value==="UI verification — 風"');
    await check('Artist edits survive browser refresh', 'document.getElementById("artist-name").value==="Sickseven UI review" && document.getElementById("artist-aliases").value.includes("Community alias 🌲")');
    await b.evaluate('document.querySelector("[data-close=catalog-dialog]").click()');
  } else {
    await b.waitFor('document.getElementById("onboarding").hidden');
    await b.evaluate('document.getElementById("open-policies").click()');
    await b.waitFor('document.querySelector("#policy-list .policy-badge.approved")');
    await check('Approved policies and onboarding survive application restart', 'document.querySelector("#policy-list .policy-badge").textContent==="Approved · Active"');
    await b.evaluate('document.querySelector("[data-close=policies-dialog]").click()');
    await b.evaluate('document.getElementById("browse-catalog").click()');
    await b.waitFor('document.getElementById("song-title").value==="UI verification — 風"');
    await b.resize(1366,900);
    await b.screenshot('docs/screenshots/catalog-editor-1366.png');
    await b.evaluate('document.getElementById("save-song").click()');
    await b.waitFor('document.getElementById("save-status").textContent==="Saved to your catalog."');
    await check('Save action remains visible outside the scrolling editor', 'document.getElementById("save-song").getBoundingClientRect().bottom < innerHeight');
    await b.evaluate('document.querySelector("[data-close=catalog-dialog]").click()');
  }
  for(const [width,height] of [[1680,941],[1366,900],[1024,768]]) {
    await b.resize(width,height);
    await b.evaluate('document.getElementById("conversation").scrollTop=document.getElementById("conversation").scrollHeight');
    await check('No horizontal overflow at '+width, 'document.documentElement.scrollWidth<=innerWidth');
    await check('Composer stays in window at '+width, 'document.getElementById("composer").getBoundingClientRect().bottom<=innerHeight');
    await shot('station-'+mode+'-'+width);
  }
  if(mode==='restart') {
    await b.resize(1366,900);
    await b.evaluate('document.querySelector("#policy-list .policy-card [data-action=discuss]").click()');
    await check('Discuss/Edit continues in the existing conversation', 'document.getElementById("message").value.includes("discuss or revise policy")');
    const request='For this isolated UI test only, propose a revision of the same existing variety policy, adding that stylistically consistent transitions are still allowed. Do not approve it and do not create another policy.';
    await b.evaluate('document.getElementById("message").value='+JSON.stringify(request)+';document.getElementById("composer").requestSubmit()');
    await b.evaluate('document.getElementById("open-policies").click()');
    await b.waitFor('document.querySelector("#policy-list .policy-card [data-action=decline]") && !document.querySelector("#policy-list .policy-card [data-action=decline]").disabled',220000);
    await b.screenshot('docs/screenshots/policy-revision-1366.png');
    await b.evaluate('document.querySelector("#policy-list .policy-card [data-action=decline]").click()');
    await b.waitFor('document.querySelector("#policy-list .policy-badge").textContent==="Declined"');
    await check('Declining replacement preserves prior approved policy and onboarding', 'document.getElementById("onboarding").hidden && document.querySelector("#policy-list .policy-reason").textContent.includes("earlier approved revision remains active")');
    await shot('policy-declined-1366');
  }
  report.browserErrors=b.errors;
  if(b.errors.length) throw new Error(JSON.stringify(b.errors));
  await writeFile('docs/ui-browser-'+mode+'.json',JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
} finally { await b.close(); }
