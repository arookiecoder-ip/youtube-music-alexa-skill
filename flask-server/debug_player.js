const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync('templates/remote.html', 'utf8');
const dom = new JSDOM(html);
let js = fs.readFileSync('templates/static/js/player.js', 'utf8');
try {
  dom.window.eval('try { ' + js + '\n} catch(e) { console.error("Caught internal:", e.stack); }');
  if (dom.window.showNowPlaying) {
    console.log('Defined OK');
  } else {
    console.log('Not defined');
  }
} catch(e) {
  console.log('Eval error:', e);
}
