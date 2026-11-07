const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function fixture() {
  const nodes = {};
  class Element {
    constructor() { this.listeners={}; this.children=[]; this.value=''; this.style={}; this.textContent=''; this.innerHTML=''; this.dataset={}; this.attrs={}; this.classes=new Set(); this.classList={add:x=>this.classes.add(x),remove:x=>this.classes.delete(x),toggle:(x,on)=>on?this.classes.add(x):this.classes.delete(x)}; }
    addEventListener(name, cb) { this.listeners[name]=cb; }
    setAttribute(k,v) { this.attrs[k]=v; }
    appendChild(child) { this.children.push(child); return child; }
    focus() {}
    remove() {}
    getAttribute(k) { return this.attrs[k]; }
  }
  const tabs = ['chat','pool','api'].map(panel=>{const e=new Element();e.dataset.panel=panel;return e;});
  const document = {documentElement: {},getElementById:id=>nodes[id]??=new Element(),createElement:()=>new Element(),querySelectorAll:selector=>selector==='.tab'?tabs:[]};
  const sandbox={document, localStorage:{getItem:()=>null,setItem:()=>{}}, location:{origin:'http://localhost'},setInterval:()=>{},setTimeout:()=>{},Date,console,AbortController,TextDecoder,navigator:{clipboard:{writeText:async()=>{}}},fetch:async()=>({json:async()=>({sessions:[],count:1,ready:1,pace:5,requests:0,errors:0,identities:[]})})};
  const source=fs.readFileSync('static/app.js','utf8').replace('  applyLang();\n  refreshSessions();', '  globalThis.testing = {mdToHtml, state, loadSession, newChat};\n  applyLang();\n  refreshSessions();');
  vm.runInNewContext(source,sandbox);
  return {nodes,tabs,testing:sandbox.testing};
}

test('all sidebar tabs work without missing element references',()=>{
 const {tabs,nodes}=fixture();
 tabs[2].listeners.click();
 assert.equal(nodes.settingsPanel.classes.has('hidden'),true);
 assert.equal(nodes.apiPanel.classes.has('hidden'),false);
 tabs[0].listeners.click();
 assert.equal(nodes.settingsPanel.classes.has('hidden'),false);
});
test('navigation cannot replace session while generation runs',async()=>{
 const {testing}=fixture();
 testing.state.sessionId='original';testing.state.streaming=true;
 testing.newChat(); await testing.loadSession('other');
 assert.equal(testing.state.sessionId,'original');
});
test('markdown escapes injected HTML and executable links',()=>{
 const {testing}=fixture();
 const html=testing.mdToHtml('<img src=x onerror=alert(1)> [bad](javascript:alert(1))');
 assert.ok(!html.includes('<img'));
 assert.ok(!html.includes('href="javascript:'));
 assert.ok(html.includes('&lt;img'));
});
test('code blocks remain code and do not run markup',()=>{
 const {testing}=fixture();
 const html=testing.mdToHtml('```html\n<script>alert(1)</script>\n```');
 assert.ok(html.includes('<pre><code>&lt;script&gt;'));
 assert.ok(!html.includes('<script>'));
});
