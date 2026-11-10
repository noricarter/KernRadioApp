// Actual UI -> Luna -> tools -> illustrated frontend -> Suno audio lifecycle.
// Only use against tests.programming_live_server (plays two complete real songs).
import {browser} from './browser_driver.mjs';
import {writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';

const url=process.argv[2];
const first='0746f5e7-4d09-4d6b-9129-733c341f23eb', second='f674159d-5e1b-4dc9-89aa-8e9c51712fb9';
const backend=await browser(url,1366,900);
let frontend;
const report={scope:'Real Luna, real Suno audio and actual Flask/browser UI in an isolated station copy',dialogue:[],renders:[]};
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function state(){return backend.evaluate('(async()=>await(await fetch("/api/broadcast")).json())()');}
async function until(predicate,timeout=240000){
  const end=Date.now()+timeout;let logged=0;
  while(Date.now()<end){
    const value=await state();
    if(value.dialogue && !report.dialogue.some(item=>item.id===value.dialogue.id && item.page===value.dialogue.page))report.dialogue.push(value.dialogue);
    if(Date.now()-logged>20000){console.log(JSON.stringify({phase:value.phase,track:value.current?.title,seconds:value.position_seconds,error:value.error}));logged=Date.now();}
    if(predicate(value))return value;
    if(value.phase==='failed')throw Error(JSON.stringify(value));
    await delay(500);
  }
  throw Error('Programming condition timed out: '+JSON.stringify(await state()));
}
async function chat(text){
  await backend.waitFor('!document.getElementById("send").disabled',180000);
  const before=await backend.evaluate('document.querySelectorAll("#messages .agent").length');
  await backend.evaluate('document.getElementById("message").value='+JSON.stringify(text)+';document.getElementById("composer").requestSubmit()');
  await backend.waitFor('document.querySelectorAll("#messages .agent").length > '+before+' && !document.getElementById("send").disabled',180000);
  return backend.evaluate('[...document.querySelectorAll("#messages .agent")].at(-1).textContent');
}
try{
  await backend.waitFor('!document.getElementById("start-programming").disabled');
  report.preparationReply=await chat('For this isolated live verification only: when I click Start Programming, begin with Mireille’s Lullaby ('+first+'), followed by I Went To The Pictures Tomorrow ('+second+'). Keep introductions and outros very brief and grounded in the songs. Do not make this a permanent policy. Wait for the Start button.');
  await backend.evaluate('document.getElementById("start-programming").click()');
  frontend=await browser(url+'/listen/',1366,900,'document.readyState === "complete" && document.getElementById("commentText")');
  const playing=await until(value=>value.phase==='playing' && value.position_seconds>2,200000);
  if(playing.current.id!==first)throw Error('Did not honor requested first song: '+JSON.stringify(playing));
  report.firstPlaying=playing;
  try{
    const inputs=JSON.parse(execFileSync('pactl',['--format=json','list','sink-inputs'],{encoding:'utf8'}));
    report.desktopAudio=inputs.filter(input=>/chrom/i.test(input.properties?.['application.process.binary']||'')).map(input=>({corked:input.corked,mute:input.mute}));
    if(!report.desktopAudio.some(output=>!output.corked && !output.mute))throw Error('No active Chrome audio output');
  }catch(error){throw Error('Desktop audio verification failed: '+error.message);}
  await frontend.waitFor('document.getElementById("trackTitle").textContent.includes("Mireille")');
  report.displayOnlyHasNoAudio=await frontend.evaluate('document.querySelectorAll(".audio-bridge").length===0');
  report.changeReply=await chat('Keep the current song playing to its end. Make '+second+' the very next song, and change the visible on-air dialogue now to exactly: "Hang onto your headphones, explorers—another musical treasure is coming up!" Do not interrupt the audio or save a policy.');
  const changed=await until(value=>value.queue[0]?.id===second && value.dialogue?.text.includes('Hang onto your headphones'),120000);
  if(changed.current.id!==first || changed.playback!=='playing')throw Error('Chat change interrupted current audio');
  report.changedDuringMusic=changed;
  for(const [width,height] of [[1366,900],[1024,768],[1680,941]]){
    await frontend.resize(width,height);
    await frontend.screenshot('docs/screenshots/programming-public-'+width+'.png');
    const render=await frontend.evaluate('({width:innerWidth,height:innerHeight,dialogueOverflow:document.getElementById("commentText").scrollHeight>document.getElementById("commentText").clientHeight,documentOverflow:document.documentElement.scrollWidth>innerWidth})');
    if(render.dialogueOverflow || render.documentOverflow)throw Error(JSON.stringify(render));
    report.renders.push(render);
  }
  const next=await until(value=>value.phase==='playing' && value.current?.id===second && value.position_seconds>2,300000);
  report.secondPlaying=next;
  await backend.waitFor('!document.getElementById("stop-programming").disabled');
  await backend.evaluate('document.getElementById("stop-programming").click()');
  const draining=await until(value=>value.stop_requested,10000);
  if(draining.current.id!==second || draining.playback!=='playing')throw Error('Stop did not drain current song');
  report.draining=draining;
  await backend.screenshot('docs/screenshots/programming-stop-requested-1366.png');
  await until(value=>value.phase==='signing_off',600000);
  await frontend.screenshot('docs/screenshots/programming-signoff-1680.png');
  const stopped=await until(value=>!value.running,60000);
  report.stopped=stopped;
  await delay(2500);
  const stable=await state();
  if(stable.running || stable.current || stable.queue.length || stable.playback!=='completed')throw Error('Unexpected playback after signoff: '+JSON.stringify(stable));
  report.noRestartAfterSignoff=true;
  report.browserErrors=[...backend.errors,...frontend.errors];
  if(report.browserErrors.length)throw Error(JSON.stringify(report.browserErrors));
  console.log('PROGRAMMING BROWSER CHECK PASSED');
}catch(error){report.failure=error.message;throw error;}
finally{
  await writeFile('docs/programming-browser-result.json',JSON.stringify(report,null,2)+'\n');
  if(frontend)await frontend.close();
  await backend.close();
}
