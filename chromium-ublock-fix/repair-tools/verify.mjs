// Local, isolated functional verification for Chromium MV2/uBlock Origin.
// Does not open or edit the user's existing browser profile.
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';

const [binary, extension, work, reportPath] = process.argv.slice(2);
if (!binary || !extension || !work || !reportPath || typeof WebSocket !== 'function') {
  throw new Error('Usage: node verify.mjs BINARY EXTENSION WORK REPORT (Node.js 22+)');
}
const manifest = JSON.parse(fs.readFileSync(path.join(extension, 'manifest.json'), 'utf8'));
const extensionId = [...crypto.createHash('sha256').update(Buffer.from(manifest.key, 'base64')).digest('hex').slice(0, 32)]
  .map(c => String.fromCharCode(97 + parseInt(c, 16))).join('');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const result = { passed: false, extension_id: extensionId, extension_version: manifest.version, phases: [] };
const liveProcesses = new Set();

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.sequence = 0;
    this.pending = new Map();
    this.events = [];
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (!message.id) { this.events.push(message); return; }
      const item = this.pending.get(message.id);
      if (!item) return;
      this.pending.delete(message.id);
      clearTimeout(item.timer);
      message.error ? item.reject(new Error(JSON.stringify(message.error))) : item.resolve(message.result);
    });
    socket.addEventListener('close', () => {
      for (const item of this.pending.values()) { clearTimeout(item.timer); item.reject(new Error('CDP closed')); }
      this.pending.clear();
    });
  }
  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { socket.close(); reject(new Error('CDP connection timeout')); }, 5000);
      socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('CDP connection failed')); }, { once: true });
    });
    return new CDP(socket);
  }
  async send(method, params = {}) {
    const id = ++this.sequence;
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 7000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const response = await this.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  }
  close() { this.socket.close(); }
}

async function until(probe, milliseconds, label) {
  const deadline = Date.now() + milliseconds;
  let lastError;
  while (Date.now() < deadline) {
    try { const value = await probe(); if (value) return value; } catch (error) { lastError = error; }
    await pause(100);
  }
  throw new Error('Timed out: ' + label + (lastError ? ': ' + lastError.message : ''));
}

let adRequests = 0;
const server = http.createServer((request, response) => {
  if (request.url.includes('/pagead/js/adsbygoogle.js')) {
    adRequests++;
    response.writeHead(200, { 'Content-Type': 'application/javascript' });
    response.end('window.uboProbeAdLoaded = true;');
    return;
  }
  response.writeHead(200, { 'Content-Type': 'text/html', 'Cache-Control': 'no-store' });
  response.end(`<!doctype html><title>uBO offline verification</title><script src="http://pagead2.googlesyndication.com:${server.address().port}/pagead/js/adsbygoogle.js"></script><p>uBlock verification</p>`);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));

function killTestGroup(child, signal) {
  if (!child.pid) return;
  try { globalThis.process.kill(-child.pid, signal); } catch {}
}

async function stopBrowser(process) {
  if (process.exitCode !== null || process.signalCode !== null) { killTestGroup(process, 'SIGTERM'); return; }
  const exited = once(process, 'exit');
  killTestGroup(process, 'SIGTERM');
  await Promise.race([exited, pause(2000)]);
  if (process.exitCode === null && process.signalCode === null) {
    // This PID/group belongs only to the isolated test started below.
    killTestGroup(process, 'SIGKILL');
    await Promise.race([exited, pause(1000)]);
  }
}

async function phase(label, profileName, loadExtension) {
  const profile = path.join(work, profileName);
  fs.mkdirSync(profile, { recursive: true });
  fs.rmSync(path.join(profile, 'DevToolsActivePort'), { force: true });
  const logPath = path.join(work, label + '.log');
  const log = fs.openSync(logPath, 'w');
  const args = [
    '--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-component-update', '--disable-sync',
    '--password-store=basic', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
    '--user-data-dir=' + profile, '--no-proxy-server',
    '--host-resolver-rules=MAP pagead2.googlesyndication.com 127.0.0.1',
  ];
  if (loadExtension) args.push('--load-extension=' + extension);
  args.push('chrome://extensions/');
  const process = spawn(binary, args, { stdio: ['ignore', 'ignore', log], detached: true });
  liveProcesses.add(process);
  let spawnError;
  process.on('error', error => { spawnError = error; });
  const connections = [];
  const summary = { label };
  try {
    const port = await until(async () => {
      if (spawnError) throw spawnError;
      if (process.exitCode !== null) throw new Error('Browser exited ' + process.exitCode);
      const file = path.join(profile, 'DevToolsActivePort');
      return fs.existsSync(file) && Number(fs.readFileSync(file, 'utf8').split('\n')[0]);
    }, 10000, 'browser startup');
    const list = async () => (await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(3000) })).json();
    const target = await until(async () => (await list()).find(item => item.type === 'page'), 5000, 'page target');
    const page = await CDP.connect(target.webSocketDebuggerUrl);
    connections.push(page);
    summary.browser = (await page.send('Browser.getVersion')).product;
    await page.send('Page.navigate', { url: 'chrome://extensions/' });
    await until(() => page.evaluate("typeof chrome.developerPrivate?.getExtensionsInfo === 'function'"), 5000, 'extensions page');
    if (loadExtension) {
      const info = await until(async () => {
        const entries = await page.evaluate('new Promise(resolve => chrome.developerPrivate.getExtensionsInfo({includeDisabled:true,includeTerminated:true},resolve))');
        return entries.find(entry => entry.id === extensionId);
      }, 8000, 'uBlock registration');
      summary.state = info.state;
      if (info.state !== 'ENABLED') throw new Error('uBlock disabled: ' + JSON.stringify(info.disableReasons));
      let background;
      summary.health = await until(async () => {
        try {
          if (!background || background.socket.readyState !== WebSocket.OPEN) {
            const target = (await list()).find(item => item.type === 'background_page' && item.url.startsWith(`chrome-extension://${extensionId}/`));
            if (!target) return false;
            background = await CDP.connect(target.webSocketDebuggerUrl);
            connections.push(background);
          }
          const value = await background.evaluate("(async()=>{if(!self.µBlock?.readyToFilter)return {ready:false};return {version:chrome.runtime.getManifest().version,listener:chrome.webRequest.onBeforeRequest.hasListeners(),ready:true,filters:(await import(chrome.runtime.getURL('js/static-net-filtering.js'))).default.getFilterCount()}})()");
          summary.last_health = value;
          return value.ready && value.listener && value.filters > 0 ? value : false;
        } catch (error) { summary.last_background_error = error.message; background?.close(); background = undefined; return false; }
      }, 25000, 'uBlock filtering engine');
    }
    adRequests = 0;
    await page.send('Network.enable');
    await page.send('Page.enable');
    const testURL = `http://127.0.0.1:${server.address().port}/${label}`;
    await page.send('Page.navigate', { url: testURL });
    await until(() => page.evaluate('location.href === ' + JSON.stringify(testURL) + " && document.readyState === 'complete'"), 6000, 'test page');
    await pause(300);
    const failures = page.events.filter(event => event.method === 'Network.loadingFailed').map(event => event.params.errorText);
    summary.ad_requests = adRequests;
    summary.failures = failures;
    if (loadExtension && !(adRequests === 0 && failures.includes('net::ERR_BLOCKED_BY_CLIENT'))) {
      throw new Error('Expected ad request was not blocked: ' + JSON.stringify(summary));
    }
    if (!loadExtension && adRequests !== 1) throw new Error('Offline control request failed: ' + JSON.stringify(summary));
    summary.passed = true;
    result.phases.push(summary);
    console.log(label + ': ' + (loadExtension ? `uBO enabled; ${summary.health.filters} filters; request blocked` : 'control request succeeds'));
  } catch (error) {
    summary.error = error.message;
    summary.browser_log = fs.readFileSync(logPath, 'utf8').slice(-3000);
    result.phases.push(summary);
    throw error;
  } finally {
    for (const connection of connections) connection.close();
    await stopBrowser(process);
    liveProcesses.delete(process);
    fs.closeSync(log);
  }
}

for (const signal of ['SIGTERM', 'SIGINT']) {
  process.once(signal, () => {
    for (const child of liveProcesses) killTestGroup(child, 'SIGKILL');
    result.error = 'Verification interrupted by ' + signal;
    fs.writeFileSync(reportPath, JSON.stringify(result, null, 2));
    process.exit(1);
  });
}

const overall = setTimeout(() => {
  for (const process of liveProcesses) killTestGroup(process, 'SIGKILL');
  result.error = 'Overall test timeout';
  fs.writeFileSync(reportPath, JSON.stringify(result, null, 2));
  process.exit(1);
}, 100000);
try {
  await phase('baseline', 'control-profile', false);
  await phase('uBlock', 'ubo-profile', true);
  await phase('restart', 'ubo-profile', true);
  result.passed = true;
  console.log('PASS: isolated enablement, offline blocking, and restart tests');
} catch (error) {
  result.error = error.message;
  console.error(error.stack || error);
  process.exitCode = 1;
} finally {
  clearTimeout(overall);
  for (const process of liveProcesses) await stopBrowser(process);
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  fs.writeFileSync(reportPath, JSON.stringify(result, null, 2) + '\n');
}
