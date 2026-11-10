// Dependency-free Chrome DevTools driver for actual browser render checks.
import {spawn} from 'node:child_process';
import {mkdtemp, readFile, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
export async function browser(url, width = 1366, height = 900, readyExpression = 'document.readyState === "complete" && document.getElementById("step-action")') {
  const profile = await mkdtemp(join(tmpdir(), 'kern-ui-chrome-'));
  const chrome = spawn('/usr/bin/google-chrome', ['--headless=new', '--disable-gpu',
    '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0',
    '--user-data-dir=' + profile, 'about:blank'], {stdio:'ignore'});
  let port;
  for (let i=0; i<120; i++) {
    try { port = (await readFile(join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]; break; }
    catch { await delay(100); }
  }
  if (!port) throw new Error('Chrome did not open its debugging port.');
  const target = await (await fetch('http://127.0.0.1:' + port + '/json/new?' + encodeURIComponent(url), {method:'PUT'})).json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject) => { socket.addEventListener('open',resolve,{once:true}); socket.addEventListener('error',reject,{once:true}); });
  let next=0;
  const pending = new Map(), errors=[];
  socket.addEventListener('message', event => {
    const value=JSON.parse(event.data);
    if (value.id) {
      const {resolve,reject} = pending.get(value.id) || {};
      pending.delete(value.id);
      if (value.error) reject?.(new Error(JSON.stringify(value.error))); else resolve?.(value.result);
    } else if (value.method === 'Runtime.exceptionThrown') errors.push(value.params.exceptionDetails);
  });
  const call = (method, params={}) => new Promise((resolve,reject) => {
    const id=++next; pending.set(id,{resolve,reject}); socket.send(JSON.stringify({id,method,params}));
  });
  await call('Runtime.enable'); await call('Page.enable');
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const resize = async (w,h) => {
    await call('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:false});
    await delay(250);
  };
  const waitFor = async (expression, timeout=30000) => {
    const end=Date.now()+timeout;
    while (Date.now()<end) { if (await evaluate(expression)) return; await delay(150); }
    throw new Error('Browser condition timed out: ' + expression);
  };
  const close = async () => {
    socket.close(); chrome.kill('SIGTERM');
    await new Promise(resolve => chrome.exitCode !== null ? resolve() : chrome.once('exit',resolve));
    await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:200});
  };
  try {
    await resize(width,height);
    await evaluate('document.fonts.ready');
    await waitFor(readyExpression);
  } catch (error) { await close(); throw error; }
  const screenshot = async path => {
    await delay(250);
    const result=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    await writeFile(path,Buffer.from(result.data,'base64'));
  };
  return {call,evaluate,resize,waitFor,screenshot,errors,close};
}
if (import.meta.url === 'file://' + process.argv[1]) {
  const b=await browser(process.argv[2],Number(process.argv[4]||1366),Number(process.argv[5]||900));
  try { await b.waitFor('document.getElementById("setup-steps").children.length === 4'); await b.screenshot(process.argv[3]); console.log(await b.evaluate('({width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth,errors:document.getElementById("error").textContent})')); }
  finally { await b.close(); }
}
