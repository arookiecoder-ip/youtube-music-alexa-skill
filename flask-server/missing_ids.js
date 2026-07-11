const fs = require('fs');
const html = fs.readFileSync('templates/remote.html', 'utf8');
const js = fs.readFileSync('templates/static/js/player.js', 'utf8');
const matches = [...js.matchAll(/getElementById\('([^']+)'\)/g)].map(m => m[1]);
const unique = [...new Set(matches)];
const missing = unique.filter(id => !html.includes('id="' + id + '"') && !html.includes("id='" + id + "'"));
console.log('Missing IDs:', missing);
