const assert = require('assert'), fs = require('fs'), vm = require('vm'), path = require('path');
const source=fs.readFileSync(path.join(__dirname,'../templates/static/js/browser-player.js'),'utf8');
const prefix=source.slice(0,source.indexOf('  const audio = new Audio();'))+'window.identity=id;})();';
let saved=null, next=0;
function open(type,blocked=false) {
 const window={api(){},Audio(){},crypto:{},performance:{getEntriesByType:()=>[{type}]},
  sessionStorage:{getItem(){if(blocked)throw Error('blocked');return saved;},setItem(k,v){if(blocked)throw Error('blocked');saved=v;}}};
 vm.runInNewContext(prefix,{window,crypto:{randomUUID:()=>String(++next)}});
 return window.identity;
}
const first=open('navigate');
assert.equal(open('reload'),first,'Refresh must keep the same browser output identity');
const duplicate=open('navigate');
assert.notEqual(duplicate,first,'A duplicated/new tab must receive its own playback identity');
assert.equal(open('reload'),duplicate);
assert.notEqual(open('reload',true),duplicate,'Blocked storage must safely generate a new identity');
console.log('PASS browser identity: refresh, duplicated tabs and unavailable storage');
