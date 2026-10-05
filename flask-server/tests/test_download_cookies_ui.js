const assert = require('node:assert/strict');
const fs = require('fs');
const vm = require('vm');
class Element {
  constructor() { this.handlers = {}; this.value = ''; this.textContent = ''; this.hidden = true; this.files = []; }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  focus() {}
  async trigger(type) { await this.handlers[type]({preventDefault() {}, target:this}); }
}
const elements = {};
const ids = ['modal','replace','close','form','text','file','save','message','check'];
ids.forEach(id => elements['download-cookies-'+id] = new Element());
elements['status-yt-cookies'] = new Element();
const responses = [];
const calls = [];
const window = { api: async (path, body) => { calls.push({path,body}); const item=responses.shift(); if (item instanceof Error) throw item; return item; } };
vm.runInNewContext(fs.readFileSync(require('path').join(__dirname,'../templates/static/js/download-cookies.js'),'utf8'), {
  window, document:{getElementById:id=>elements[id],addEventListener(){}}, Error
});
const element = id => elements['download-cookies-'+id];
(async () => {
  elements['status-yt-cookies'].textContent = 'Download OK';
  await element('replace').trigger('click');
  assert.equal(element('modal').hidden,false,'replacement available even when valid');
  await element('close').trigger('click');
  elements['status-yt-cookies'].textContent = 'Download failed';
  await element('replace').trigger('click');
  assert.equal(element('modal').hidden,false,'replacement available even when invalid');
  element('text').value='candidate-cookie';
  responses.push(new Error('Audio download failed. Existing cookies were kept.'));
  await element('form').trigger('submit');
  assert.equal(element('text').value,'candidate-cookie');
  assert.match(element('message').textContent,/Existing cookies were kept/);
  assert.equal(element('save').disabled,false,'failure permits retry');
  responses.push({success:true,message:'Audio sample downloaded successfully.'});
  await element('form').trigger('submit');
  assert.equal(element('text').value,'','successful save clears credentials');
  assert.equal(elements['status-yt-cookies'].textContent,'Download OK');
  element('file').files=[{size:42,text:async()=>'file-cookie'}];
  responses.push({success:true,message:'ok'});
  await element('form').trigger('submit');
  assert.equal(calls.at(-1).body.cookies,'file-cookie');
  element('file').files=[];
  const count=calls.length;
  await element('form').trigger('submit');
  assert.equal(calls.length,count,'empty input never sent');
  responses.push({valid:false,message:'YouTube blocked download'});
  await element('check').trigger('click');
  assert.match(calls.at(-1).path,/refresh=1/);
  assert.equal(elements['status-yt-cookies'].textContent,'Download failed');
  await element('close').trigger('click');
  assert.equal(element('text').value,'');
  assert.equal(element('file').value,'');
  console.log('PASS replacement, rejection/retry, file upload, fresh check, and credential cleanup');
})().catch(error => {console.error(error);process.exitCode=1;});
