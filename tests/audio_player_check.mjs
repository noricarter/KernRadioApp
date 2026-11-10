// Deterministic tests of the actual player.js; Audio/fetch/clock are fakes.
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile('services/suno_playback/player.js','utf8');
async function player(resume=0){
  let now=0,next=0;
  const timers=new Map(),accepted=[],created=[];
  const fault={command:false,event:false,once:0};
  let command={revision:1,action:'play',session:'test-session',src:'local-audio',resume_seconds:resume};
  class Audio {
    constructor(){this.paused=true;this.ended=false;this.currentTime=0;this.duration=120;this.handlers={};this.plays=0;created.push(this);}
    addEventListener(kind,fn){(this.handlers[kind]??=[]).push(fn);}
    emit(kind){if(kind==='ended')this.ended=true;for(const fn of this.handlers[kind]||[])fn();}
    play(){this.plays++;this.paused=false;this.emit('playing');return Promise.resolve();}
    pause(){if(!this.paused){this.paused=true;this.emit('pause');}}
    load(){if(this.src)this.emit('loadedmetadata');}
    removeAttribute(name){if(name==='src')this.src='';}
  }
  const schedule=(fn,ms,repeat=0)=>{const id=++next;timers.set(id,{fn,at:now+ms,repeat});return id;};
  const context=vm.createContext({Audio,location:{pathname:'/private/'},performance:{now:()=>now},
    AbortSignal:{timeout:()=>undefined},console:{warn(){},error(){}},
    setTimeout:(fn,ms)=>schedule(fn,ms),setInterval:(fn,ms)=>schedule(fn,ms,ms),
    fetch:async(path,options)=>{
      if(path.endsWith('command')){
        if(fault.command||fault.once-->0)throw Error('temporary command loss');
        return {ok:true,json:async()=>({...command})};
      }
      if(fault.event)return {ok:false};
      accepted.push(JSON.parse(options.body));return {ok:true};
    }});
  const settle=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
  vm.runInContext(source,context);await settle();
  async function advance(ms){
    const end=now+ms;
    for(let count=0;count<10000;count++){
      const entries=[...timers.entries()].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at);
      if(!entries.length){now=end;await settle();return;}
      const [id,t]=entries[0];now=t.at;timers.delete(id);
      if(t.repeat)timers.set(id,{...t,at:now+t.repeat});
      await t.fn();await settle();
    }
    throw Error('Timer loop did not settle');
  }
  return {fault,accepted,created,advance,run:code=>vm.runInContext(code,context),setCommand:value=>{command=value;}};
}

let p=await player();
p.fault.once=1;await p.advance(300);
assert.equal(p.created[0].paused,false,'one failed poll must not pause audio');
assert.equal(p.created[0].plays,1);
console.log('PASS: brief control loss keeps the same audio playing');

p=await player();const original=p.created[0];original.currentTime=27;
p.fault.command=true;await p.advance(6000);
assert.equal(original.paused,true);
assert(p.accepted.some(e=>e.kind==='control_paused'));
p.fault.command=false;await p.advance(300);
assert.equal(p.created.length,1);assert.equal(original.paused,false);
assert.equal(original.currentTime,27);assert.equal(original.plays,2);
assert(!p.accepted.some(e=>e.kind==='error'));
console.log('PASS: sustained loss pauses, reports it, then resumes the same position');

p=await player();p.fault.command=true;await p.advance(6000);
p.setCommand({revision:2,action:'stop',session:'test-session'});
p.fault.command=false;await p.advance(300);
assert.equal(p.created[0].plays,1);assert.equal(p.run('audio'),null);
assert(p.accepted.some(e=>e.kind==='stopped'));
assert(!p.accepted.some(e=>e.kind==='error'));
console.log('PASS: a Stop received on reconnect prevents resuming');

p=await player();p.fault.event=true;p.created[0].emit('ended');await p.advance(1100);
assert(!p.accepted.some(e=>e.kind==='ended'));
p.fault.event=false;await p.advance(1100);
assert.equal(p.accepted.filter(e=>e.kind==='ended').length,1);
console.log('PASS: a rejected end event is retried until acknowledged');

p=await player(37);assert.equal(p.created[0].currentTime,37);
console.log('PASS: a reloaded player starts from the backend resume position');

p.created[0].pause();await p.advance(300);
assert(p.accepted.some(e=>e.kind==='error'&&e.error.includes('paused the song unexpectedly')));
console.log('PASS: an unexpected media pause is reported');

p=await player();p.fault.event=true;
for(let i=0;i<1000;i++)p.run("report('progress',session)");
await p.advance(2100);assert(p.run('pending.length')<5);
p.fault.event=false;await p.advance(1500);
assert(p.accepted.some(e=>e.kind==='heartbeat'));
console.log('PASS: progress is coalesced and heartbeats continue');
