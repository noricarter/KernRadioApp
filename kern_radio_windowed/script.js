'use strict';
// Preserve the illustrated assets' proportions while extending the scenic
// canvas into the extra room of a normal browser window.
const REFERENCE_WIDTH = 1672;
const REFERENCE_HEIGHT = 941;
const viewport = document.getElementById('viewport');
const scene = document.getElementById('scene');
function fitScene() {
  const width = viewport.clientWidth;
  const height = viewport.clientHeight;
  if (!width || !height) return;
  // Uniform scale: the mascot, woodwork and artwork are never squashed.
  const scale = Math.min(width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT);
  const designWidth = width / scale;
  const designHeight = height / scale;
  const extraX = Math.max(0, designWidth - REFERENCE_WIDTH);
  const extraY = Math.max(0, designHeight - REFERENCE_HEIGHT);
  scene.style.width = `${designWidth}px`;
  scene.style.height = `${designHeight}px`;
  scene.style.setProperty('--extra-x', `${extraX}px`);
  scene.style.setProperty('--extra-y', `${extraY}px`);
  scene.style.transform = `scale(${scale})`;
}
window.addEventListener('resize', fitScene, {passive:true});
if (window.visualViewport) window.visualViewport.addEventListener('resize',fitScene,{passive:true});
new ResizeObserver(fitScene).observe(viewport);
fitScene();
const queue=document.getElementById('queue');
const cover=document.getElementById('cover');
const albumArt=document.getElementById('albumArt');
const fireflies=document.getElementById('fireflies');
let mascot=document.getElementById('hostMascot');
const mascotNames=new Set(['standard','cute','headphones','mischief','surprise','sad','consider','explain','excited']);
let mascotChoice='standard';
function setMascot(value){
  const name=mascotNames.has(value)?value:'standard';
  if(name===mascotChoice)return;
  mascotChoice=name;
  // Load only the requested asset; keep the last image visible until ready.
  // The choice check prevents an older, slower request replacing a newer one.
  const image=new Image();
  image.id=mascot.id;image.className=mascot.className;image.decoding='async';
  image.dataset.mascot=name;image.alt='Our expedition guide — '+name+' expression';
  image.onload=()=>{
    if(mascotChoice!==name)return;
    mascot.replaceWith(image);mascot=image;
  };
  image.onerror=()=>{if(mascotChoice===name && name!=='standard')setMascot('standard');};
  image.src='assets/mascot_'+name+'.png';
}
let activeId=null, trackKey=null, queueKey=null, dialogueKey=null, animation=null;
function arriveCover(){
  cover.classList.remove('song-arrival');
  void cover.offsetWidth;
  cover.classList.add('song-arrival');
  cover.addEventListener('animationend',()=>cover.classList.remove('song-arrival'),{once:true});
}
function setTrack(track){
  const key=JSON.stringify(track);
  if(key===trackKey)return;
  const changedSong=(track?.id??null)!==activeId;
  trackKey=key;
  activeId=track?.id??null;
  document.getElementById('trackTitle').textContent=track?.title??'Kern Radio';
  document.getElementById('artistName').textContent=track?.artist??'Ready for an adventure';
  albumArt.replaceChildren();
  if(track?.artworkUrl){let img=new Image();img.alt='Song artwork';img.src=track.artworkUrl;albumArt.appendChild(img);}
  if(track && changedSong)arriveCover();
  for(const node of queue.children){node.classList.toggle('active',node.dataset.id===activeId);node.setAttribute('aria-current',node.dataset.id===activeId?'true':'false');}
  fitText(document.getElementById('trackTitle'),72,26,210);
  fitText(document.getElementById('artistName'),35,20,85);
}
function setQueue(tracks){
  const key=JSON.stringify(tracks.map(track=>track.id??''));
  if(key===queueKey)return;
  queueKey=key;
  queue.replaceChildren();
  for (const track of tracks){
    const item=document.createElement('div');
    item.className='queue-item';item.dataset.id=track.id??'';
    const thumb=document.createElement('span');thumb.className='thumb';
    if(track.artworkUrl){const image=new Image();image.alt='';image.src=track.artworkUrl;thumb.appendChild(image);}
    const body=document.createElement('span');body.className='text';
    const title=document.createElement('strong');title.textContent=track.title??'Untitled';
    const artist=document.createElement('small');artist.textContent=track.artist??'Unknown artist';
    body.append(title,artist);item.append(thumb,body);queue.appendChild(item);
  }
  for(const node of queue.children){node.classList.toggle('active',node.dataset.id===activeId);}
  queue.classList.remove('queue-jiggle');
  void queue.offsetWidth;
  queue.classList.add('queue-jiggle');
  queue.addEventListener('animationend',()=>queue.classList.remove('queue-jiggle'),{once:true});
}
function randomizeFirefly(dot,initial=false){
  dot.style.left=(3+Math.random()*94)+'%';
  dot.style.top=(8+Math.random()*87)+'%';
  dot.style.width=dot.style.height=(2+Math.random()*3)+'px';
  const x=(Math.random()-.5)*100, y=(Math.random()-.5)*100;
  dot.style.setProperty('--fly-x',x+'px');
  dot.style.setProperty('--fly-y',y+'px');
  dot.style.setProperty('--fly-half-x',(x*.5)+'px');
  dot.style.setProperty('--fly-half-y',(y*.5)+'px');
  dot.style.setProperty('--glow',Math.round(5+Math.random()*9)+'px');
  if(initial){
    const duration=5+Math.random()*10;
    dot.style.animationDuration=duration+'s';
    dot.style.animationDelay=(-Math.random()*duration)+'s';
  }
}
for(let i=0;i<20;i++){
  const dot=document.createElement('i');
  randomizeFirefly(dot,true);
  dot.addEventListener('animationiteration',()=>randomizeFirefly(dot));
  fireflies.appendChild(dot);
}
const eq=document.getElementById('equalizer');
for(let i=0;i<35;i++){
 const bar=document.createElement('i');bar.style.height=(11+(i*19+7)%36)+'px';bar.style.animationDelay=(-i*.091)+'s';eq.appendChild(bar);
}
function fitText(element,start,minimum,height){
  element.style.fontSize=start+'px';
  while(element.scrollHeight>height && start>minimum)element.style.fontSize=(--start)+'px';
}
function showDialogue(dialogue){
  if(!dialogue)return;
  const key=dialogue.id+':'+dialogue.page;
  if(key===dialogueKey)return;
  dialogueKey=key;
  if(animation)cancelAnimationFrame(animation);
  const element=document.getElementById('commentText');
  element.textContent=dialogue.text;
  fitText(element,30,18,element.clientHeight);
  element.setAttribute('aria-label',dialogue.text);
  const characters=Array.from(dialogue.text);
  const started=performance.now()-dialogue.elapsed_seconds*1000;
  const duration=Math.max(1,dialogue.reveal_seconds*1000);
  const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
  function draw(now){
    const fraction=reduced?1:Math.min(1,Math.max(0,(now-started)/duration));
    element.textContent=characters.slice(0,Math.ceil(characters.length*fraction)).join('');
    if(fraction<1)animation=requestAnimationFrame(draw);else animation=null;
  }
  animation=requestAnimationFrame(draw);
}
const labels={planning:'PREPARING',greeting:'HOST’S WELCOME',introducing:'UP NEXT',starting:'LOADING',playing:'ON AIR',outro:'RADIO HOST',signing_off:'SIGNING OFF',playback_error:'STAND BY',idle:'OFF AIR',failed:'OFF AIR'};
function applyState(state){
  setMascot(state.mascot);
  const current=state.current || (state.phase==='signing_off'?state.last_played:state.running?state.queue?.[0]:null);
  setTrack(current);
  setQueue([...(state.current?[state.current]:state.phase==='signing_off' && state.last_played?[state.last_played]:[]),...(!state.stop_requested?state.queue||[]:[])]);
  const audioStatus=state.phase==='playing' && state.playback==='buffering'?(state.playback_error?'RECONNECTING AUDIO':'BUFFERING')
    :state.phase==='playing' && state.playback==='failed'?'AUDIO ERROR':null;
  document.getElementById('broadcastStatus').textContent=audioStatus || (state.stop_requested && state.phase==='playing'?'FINAL TRACK':labels[state.phase]||'OFF AIR');
  scene.classList.toggle('music-playing',state.playback==='playing');
  showDialogue(state.dialogue);
}
async function refreshBroadcast(){
  try{
    const response=await fetch('/api/broadcast');
    if(!response.ok)throw Error('Station unavailable');
    applyState(await response.json());
  }catch{
    document.getElementById('broadcastStatus').textContent='RECONNECTING';
    scene.classList.remove('music-playing');
  }
  setTimeout(refreshBroadcast,1000);
}
// The owned station window embeds the existing audio bridge. Other display
// windows render state only, so opening a second view never doubles the audio.
const bridge=new URLSearchParams(location.search).get('audio_bridge');
if(bridge){
  try{
    const address=new URL(bridge);
    if(address.protocol==='http:' && address.hostname==='127.0.0.1' && /^\/[A-Za-z0-9_-]{20,}\/$/.test(address.pathname)){
      const frame=document.createElement('iframe');frame.src=address.href;frame.allow='autoplay';frame.title='Station audio';frame.className='audio-bridge';document.body.append(frame);
    }
  }catch{}
}
setTrack(null);setQueue([]);
if(location.protocol!=='file:')refreshBroadcast();
window.KernRadioView={setTrack,setQueue,fitScene,applyState};
