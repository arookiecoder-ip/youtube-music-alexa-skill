const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const source = fs.readFileSync(path.join(__dirname, '../templates/static/js/search.js'), 'utf8');
const start = source.indexOf("     if (topResult && topResult.resultType === 'video') {");
const end = source.indexOf('     if (topResult) {', start + 1);
assert(start >= 0 && end > start, 'Hero promotion block must exist');
function hero(video, mixed, songs) {
  const context = {topResult: video, remaining: mixed, data: {songs}, topSongs: []};
  vm.runInNewContext(source.slice(start, end), context);
  return context.topResult;
}
const video = {resultType:'video', title:'Go (Official Video)', videoId:'AAAAAAAAAAA', artists:[{name:'Atif Aslam'}]};
const audio = {resultType:'song', title:'Go', video_id:'BBBBBBBBBBB', artists:[{name:'Atif Aslam'}]};
assert.equal(hero(video, [], [audio]), audio, 'Filtered audio songs must be eligible even when absent from mixed results');
assert.equal(hero(video, [], [{...audio, artists:[{name:'Other Singer'}]}]), video);
assert.equal(hero(video, [], [{...audio, title:'Other Song'}]), video);
assert.equal(hero({...video, title:'Go Official Music Video'}, [], [audio]), audio);
const unicode = {...audio, title:'আমার গান'};
assert.equal(hero({...video, title:'আমার গান (Official)'}, [], [unicode]), unicode);
assert.equal(hero(video, [{...audio, video_id:''}], []), video);
console.log('PASS search hero: filtered audio counterpart, regional video fallback, artist and Unicode matching');
