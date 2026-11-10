// Use only tests.mascot_live_server; /test/mascot is an isolated fixture adapter.
import {browser} from './browser_driver.mjs';
import {writeFile} from 'node:fs/promises';
const base=process.argv[2];
const b=await browser(base+'/listen/',1366,900,'document.getElementById("hostMascot")?.naturalWidth>0');
const names=['standard','cute','headphones','mischief','surprise','sad','consider','explain','excited'];
const report={scope:'Real Chrome/Flask and real Luna preview; no audio playback.',renders:[]};
const request=async (path,body) => {
  const html=await(await fetch(base+'/')).text();
  const token=html.match(/name="app-token" content="([^"]+)"/)[1];
  const response=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json','X-App-Token':token},body:JSON.stringify(body)});
  const value=await response.json();
  if(!response.ok)throw Error(JSON.stringify(value));
  return value;
};
try {
  // Compare image layout without the existing decorative four-pixel bob.
  await b.call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await request('/test/mascot',{mascot:'standard'});
  await b.waitFor(`document.getElementById('hostMascot').dataset.mascot==='standard'`);
  if(await b.evaluate('document.getElementById("hostMascot").dataset.mascot')!=='standard')throw Error('Wrong default');
  for(const name of names){
    await request('/test/mascot',{mascot:name});
    await b.waitFor(`document.getElementById('hostMascot').dataset.mascot===${JSON.stringify(name)} && document.getElementById('hostMascot').naturalWidth>0`);
    for(const [width,height] of [[1366,900],[1024,768],[1680,941]]){
      await b.resize(width,height);
      const render=await b.evaluate(`(()=>{const image=document.getElementById('hostMascot'),r=image.getBoundingClientRect(),text=document.querySelector('.commentary').getBoundingClientRect();return {name:image.dataset.mascot,width:innerWidth,height:innerHeight,assetWidth:image.naturalWidth,assetHeight:image.naturalHeight,left:r.left,top:r.top,right:r.right,bottom:r.bottom,boxWidth:r.width,boxHeight:r.height,fit:getComputedStyle(image).objectFit,textOverlap:r.right>text.left}})()`);
      if(render.left<0||render.top<0||render.right>width||render.bottom>height||render.fit!=='contain')throw Error(JSON.stringify(render));
      report.renders.push(render);
    }
    if(['standard','headphones','mischief'].includes(name))await b.screenshot('docs/screenshots/mascot-'+name+'-1680.png');
  }
  const baseline=report.renders.filter(r=>r.name==='standard');
  for(const r of report.renders){const base=baseline.find(b=>b.width===r.width);if(Math.abs(base.boxWidth-r.boxWidth)>.1||Math.abs(base.boxHeight-r.boxHeight)>.1||Math.abs(base.bottom-r.bottom)>.1)throw Error('Mascot layout shifted');}
  // Invalid display state cannot select an external file/path.
  await b.evaluate(`window.KernRadioView.applyState({phase:'idle',queue:[],mascot:'../../outside'})`);
  await b.waitFor(`document.getElementById('hostMascot').dataset.mascot==='standard'`);
  report.invalidStateFallsBack=true;
  await request('/api/messages',{text:'Preview the headphones host mascot in the public frontend now using the available tool. Change only the image. Do not start programming, play audio, or propose a policy.'});
  const deadline=Date.now()+180000;
  let state;
  while(Date.now()<deadline){
    state=await(await fetch(base+'/api/status')).json();
    if(!state.busy && state.messages.some(m=>m.role==='agent'))break;
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
  if(state?.busy)throw Error('Agent did not finish');
  await b.waitFor(`document.getElementById('hostMascot').dataset.mascot==='headphones'`);
  const broadcast=await(await fetch(base+'/api/broadcast')).json();
  if(broadcast.running||broadcast.playback!=='idle'||broadcast.mascot!=='headphones')throw Error('Image-only command changed playback');
  report.realAgentImageOnlySwitch=true;
  report.reply=state.messages.filter(m=>m.role==='agent').at(-1)?.text;
  report.browserErrors=b.errors;
  if(b.errors.length)throw Error(JSON.stringify(b.errors));
  await writeFile('docs/mascot-browser-result.json',JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({renders:report.renders.length,realAgentImageOnlySwitch:true,browserErrors:b.errors}));
}finally{await b.close();}
