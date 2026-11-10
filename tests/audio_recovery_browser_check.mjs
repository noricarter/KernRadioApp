// Attach only to the isolated browser owned by audio_recovery_live_check.py.
import {writeFile} from 'node:fs/promises';
const debugPort=process.argv[2],base=process.argv[3];
const targets=await(await fetch('http://127.0.0.1:'+debugPort+'/json/list')).json();
const page=targets.find(t=>t.type==='page'&&t.url.startsWith(base+'/listen/'));
if(!page)throw Error('Isolated station page missing');
const bridgeOrigin=new URL(new URL(page.url).searchParams.get('audio_bridge')).origin;
const socket=new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true})});
let next=0;
const pending=new Map(),contexts=new Map(),report={checks:[]};
socket.addEventListener('message',event=>{
 const value=JSON.parse(event.data);
 if(value.id){const task=pending.get(value.id);pending.delete(value.id);if(value.error)task?.reject(Error(JSON.stringify(value.error)));else task?.resolve(value.result);}
 else if(value.method==='Runtime.executionContextCreated')contexts.set(value.params.context.id,value.params.context);
 else if(value.method==='Runtime.executionContextDestroyed')contexts.delete(value.params.executionContextId);
 else if(value.method==='Runtime.executionContextsCleared')contexts.clear();
});
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++next;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}))});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function audio(expression){
 const context=[...contexts.values()].filter(c=>c.origin===bridgeOrigin&&c.auxData?.isDefault).at(-1);
 if(!context)throw Error('Audio frame context not ready');
 const result=await call('Runtime.evaluate',{expression,contextId:context.id,returnByValue:true,awaitPromise:true});
 if(result.exceptionDetails)throw Error(result.exceptionDetails.text);
 return result.result.value;
}
async function wait(predicate,timeout=15000){const end=Date.now()+timeout;while(Date.now()<end){try{if(await predicate())return;}catch{}await delay(100)}throw Error('Browser recovery condition timed out');}
const state=async()=>await(await fetch(base+'/test/audio')).json();
const check=name=>{report.checks.push(name);console.log('PASS: '+name)};
try{
 await call('Runtime.enable');await call('Page.enable');
 await wait(()=>audio('audio && !audio.paused && audio.currentTime>1'));
 const first=await state(),eventId=first.status.event_id;
 await audio(`globalThis.realFetch=fetch;globalThis.blockCommands=false;globalThis.blockEvents=false;globalThis.failOnce=1;fetch=(path,options)=>{if(String(path).endsWith('command')&&(blockCommands||failOnce-->0))return Promise.reject(Error('Injected command failure'));if(String(path).endsWith('event')&&blockEvents)return Promise.reject(Error('Injected event failure'));return realFetch(path,options);}`);
 await delay(800);
 if(!await audio('audio && !audio.paused'))throw Error('Brief failure paused audio');
 check('one failed control request does not stop real playback');
 await audio('blockCommands=true');
 await wait(()=>audio('connectionPaused && audio.paused'));
 const held=await audio('audio.currentTime');
 await wait(async()=> (await state()).status.state==='buffering');
 await audio('blockCommands=false');
 await wait(()=>audio('audio && !audio.paused && audio.currentTime>'+JSON.stringify(held+1)));
 if((await state()).status.event_id!==eventId)throw Error('Recovery duplicated the history start');
 check('longer control loss pauses and resumes the same audio/history event');
 const beforeReload=await audio('audio.currentTime');
 await call('Page.reload');
 await wait(()=>audio('audio && !audio.paused && audio.currentTime>'+JSON.stringify(Math.max(0,beforeReload-1))));
 const resumed=await audio('audio.currentTime');
 if((await state()).history.length!==1)throw Error('Reload created extra history');
 report.reload={before_seconds:beforeReload,after_seconds:resumed};
 check('reloading /listen/ resumes near the saved position instead of zero');
 await audio(`globalThis.realFetch=fetch;globalThis.blockEvents=true;fetch=(path,options)=>String(path).endsWith('event')&&blockEvents?Promise.reject(Error('Injected event failure')):realFetch(path,options);audio.currentTime=audio.duration-.3;`);
 await wait(()=>audio('audio && audio.ended'));
 if((await state()).history[0].outcome!==null)throw Error('History completed before its end notification arrived');
 await audio('blockEvents=false');
 await wait(async()=> (await state()).status.state==='completed');
 check('actual end event survives a failed notification and completes history once');
 await call('Page.reload');await delay(1200);
 if(await audio('audio!==null'))throw Error('Reload replayed an already completed song');
 check('a completed song is not replayed on reload');
 const html=await(await fetch(base+'/')).text();const token=html.match(/name="app-token" content="([^"]+)"/)[1];
 const replay=await fetch(base+'/test/replay',{method:'POST',headers:{'X-App-Token':token}});
 if(!replay.ok)throw Error('Could not start isolated timeout test');
 await wait(()=>audio('audio && !audio.paused'));
 await audio(`globalThis.realFetch=fetch;fetch=(path,options)=>String(path).endsWith('event')?Promise.reject(Error('Injected total event-channel loss')):realFetch(path,options);`);
 await wait(async()=> (await state()).status.state==='failed',22000);
 await wait(()=>audio('audio===null || audio.paused'));
 const final=await state();
 if(final.history.length!==2||final.history[0].outcome!=='completed'||final.history[1].outcome!=='interrupted')throw Error('Incorrect history outcomes');
 report.final=final;
 check('missing browser reports produce a visible failure and interrupted history');
 await writeFile('docs/audio-recovery-live-result.json',JSON.stringify(report,null,2)+'\n');
}finally{socket.close();}
