"""Artist shelves, collection routing, device text and Jam geometry in Chromium."""
import json
from pathlib import Path
import threading
import pytest
from flask import Flask, render_template
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def origin():
    app = Flask(__name__, template_folder=str(ROOT/'templates'), static_folder=str(ROOT/'templates/static'))
    @app.get('/<kind>')
    def page(kind):
        return render_template('jam.html' if kind == 'jam' else 'remote.html', asset_v='test')
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    thread.join()


@pytest.fixture
def page(origin):
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width':1280,'height':800})
        page.route('**/*', lambda route: route.abort() if route.request.resource_type == 'script' else route.continue_())
        page.goto(origin+'/remote')
        page.evaluate("""() => {
            document.body.classList.remove('preload');
            window.__appState = {};
            window._route = '#artist/UCtest';
            window.getRoute = () => window._route;
            window.navigateTo = route => { window._route = route; };
            window.fixture = {artist:{name:'Artist'}, topSongs:[], albums:[{browseId:'MPREone',title:'Album'}], playlists:[{playlistId:'PLone',title:'Own playlist'}], featured:[{playlistId:'PLfeatured',title:'Featured playlist'}]};
            window.api = async path => path.includes('subscribed_artists') ? {artists:[]} : window.fixture;
            document.getElementById('artist-section').hidden=false;
        }""")
        page.evaluate((ROOT/'templates/static/js/collection-renderer.js').read_text())
        page.evaluate('''() => {
            document.body.classList.add('artist-route');
            window.artistLinksHtml = (names, ids) => { const link=document.createElement('a');link.className='artist-name';link.textContent=names;link.dataset.channelId=ids[0];link.href='#artist/'+ids[0];return link.outerHTML; };
            document.addEventListener('click', event => { const artist=event.target.closest('.artist-name');if(artist){event.preventDefault();window._route='#artist/'+artist.dataset.channelId;} });
        }''')
        page.evaluate((ROOT/'templates/static/js/artist.js').read_text())
        yield page
        browser.close()


def test_optional_shelves_navigate_to_playlists_and_hide_when_empty(page):
    page.evaluate("window.loadArtist('UCtest')")
    expect(page.locator('#artist-playlists')).to_be_visible()
    expect(page.locator('#artist-featured')).to_be_visible()
    page.locator('#artist-featured-track .hscroll-card-title').click()
    assert page.evaluate('window._route') == '#playlist/PLfeatured'
    page.evaluate("window._route='#artist/UCtest'")
    page.locator('#artist-albums .artist-collection-all').click()
    assert page.evaluate('window._route') == '#artist/UCtest?collection=albums'
    page.evaluate("window.fixture={artist:{name:'Empty'},topSongs:[],playlists:[],featured:[]};window._route='#artist/UCempty';window.loadArtist('UCempty')")
    expect(page.locator('#artist-playlists')).to_be_hidden()
    expect(page.locator('#artist-featured')).to_be_hidden()


def test_collection_pages_scroll_to_fetch_and_keep_playlist_identity(page):
    page.evaluate("""() => {
        window._route='#artist/UCtest?collection=featured';
        window.api=async path => path.includes('offset=0') ? {items:Array.from({length:30},(_,i)=>({playlistId:'PL'+i,title:'Playlist '+i})),next_offset:30,has_more:true} : {items:[{playlistId:'PL0',title:'Duplicate'},{playlistId:'PLtwo',title:'Two'}],next_offset:32,has_more:false};
        return window.loadArtistReleases('UCtest','featured');
    }""")
    expect(page.locator('#artist-release-grid .hscroll-card')).to_have_count(30)
    page.locator('.artist-collection-sentinel').scroll_into_view_if_needed()
    expect(page.locator('#artist-release-grid .hscroll-card')).to_have_count(31)
    expect(page.locator('.artist-collection-sentinel')).to_be_hidden()
    page.locator('#artist-release-grid .hscroll-card-title').last.click()
    assert page.evaluate('window._route') == '#playlist/PLtwo'
    assert page.locator('.artist-collection-more').count() == 0


def test_collection_failure_retries_without_losing_destination(page):
    page.evaluate("""() => {
        window._route='#artist/UCtest?collection=albums'; let count=0;
        window.api=async () => { if (!count++) throw new Error('Offline');return {items:[],has_more:false,next_offset:0}; };
        return window.loadArtistReleases('UCtest','albums');
    }""")
    expect(page.locator('.artist-collection-status')).to_have_text('Offline')
    page.locator('.artist-collection-retry').click()
    expect(page.locator('.artist-collection-status')).to_have_text('No collections available')


def test_loading_uses_cover_skeletons_without_load_more_text(page):
    page.evaluate("""() => {
        window._route='#artist/UCtest?collection=albums';
        window.api=()=>new Promise(resolve=>window.resolveCollection=resolve);
        window.loadArtistReleases('UCtest','albums');
    }""")
    expect(page.locator('.artist-collection-skeleton')).to_have_count(12)
    expect(page.locator('#artist-release-grid')).to_have_attribute('aria-busy','true')
    assert page.get_by_text('Load more',exact=True).count()==0
    page.evaluate("window.resolveCollection({items:[{browseId:'MPREtest',title:'Album'}],next_offset:1,has_more:false})")
    expect(page.locator('.artist-collection-skeleton')).to_have_count(0)
    expect(page.locator('#artist-release-grid')).to_have_attribute('aria-busy','false')


def test_cover_card_geometry_controls_and_clickable_credits_match_other_shelves(page):
    page.evaluate("""() => {
        window.fixture.playlists=Array.from({length:20},(_,i)=>({playlistId:'PL'+i,title:'Playlist '+i,artists:[{name:'Artist name',id:'UCcredit'}]}));
        window.fixture.featured[0].artists=[{name:'Artist name',id:'UCcredit'}];
        window.fixture.additionalShelves=[{title:'Live performances',items:Array.from({length:20},(_,i)=>({kind:'track',video_id:'live'+i,title:'Live song '+i,artists:[{name:'Live artist',id:'UClive'}],artist:'Live artist'}))}];
        window.playFromQueue=()=>window.played=true;
        return window.loadArtist('UCtest');
    }""")
    for shelf in ('artist-albums','artist-playlists','artist-featured','artist-extra-0'):
        art=page.locator('#'+shelf+' .hscroll-card-art').first
        box=art.bounding_box()
        assert abs(box['width']-box['height'])<1
        title=page.locator('#'+shelf+' .hscroll-card-title').first.bounding_box()
        assert title['y'] >= box['y']+box['height']
    assert page.locator('#artist-section .playlist-card').count()==0
    controls=page.locator('#artist-playlists .hscroll-controls')
    assert controls.locator('.artist-view-all-btn').count()==1
    button=controls.locator('.artist-view-all-btn').bounding_box()
    arrow=controls.locator('.hscroll-scroll-next').bounding_box()
    assert abs((button['y']+button['height']/2)-(arrow['y']+arrow['height']/2))<1
    assert 0 <= button['x']-(arrow['x']+arrow['width']) <= 12
    expect(page.locator('#artist-extra-shelves .hscroll-scroll-next')).to_be_visible()
    page.locator('#artist-extra-0 .artist-name').first.click()
    assert page.evaluate('window._route')=='#artist/UClive'
    assert not page.evaluate('!!window.played')
    import os
    if os.environ.get('MUSICBOX_VISUAL_OUTPUT'):
        target=Path(os.environ['MUSICBOX_VISUAL_OUTPUT']);target.mkdir(parents=True,exist_ok=True)
        page.locator('#artist-playlists').screenshot(path=str(target/'artist-playlists-shelf.png'))


def test_long_device_name_does_not_move_arrow(page):
    page.locator('.device-section-inline').evaluate('(el)=>el.hidden=false')
    label = page.locator('#device-trigger > span')
    label.evaluate('(el)=>el.textContent="Phone"')
    before = page.locator('#device-trigger .cs-arrow').bounding_box()
    label.evaluate('(el)=>el.textContent="samsung SM-S926B with a very long device name"')
    after = page.locator('#device-trigger .cs-arrow').bounding_box()
    assert abs(before['x']-after['x']) < 1
    assert after['width'] == before['width']


@pytest.mark.parametrize('width,height',[(390,844),(800,600),(1280,800)])
def test_jam_player_uses_full_canvas_without_phantom_rail(page,origin,width,height):
    page.set_viewport_size({'width':width,'height':height})
    page.goto(origin+'/jam')
    page.evaluate("document.body.classList.add('now-playing-route');document.getElementById('now-playing-section').hidden=false")
    page.wait_for_timeout(400)
    box = page.locator('#now-playing-section').bounding_box()
    assert abs(box['x']) < 1
    assert abs(box['width']-width) < 2, page.evaluate("JSON.stringify({box:document.getElementById('now-playing-section').getBoundingClientRect().toJSON(),style:getComputedStyle(document.getElementById('now-playing-section')).cssText,right:getComputedStyle(document.getElementById('now-playing-section')).right,width:getComputedStyle(document.getElementById('now-playing-section')).width,maxWidth:getComputedStyle(document.getElementById('now-playing-section')).maxWidth,body:document.body.clientWidth,doc:document.documentElement.clientWidth})")
    if width < 900:
        art = page.locator('.np-page-art').bounding_box()
        assert art['width'] >= width
        assert abs(art['height']-height*.5) < 2


def test_extra_artist_shelves_keep_track_and_album_actions(page):
    page.evaluate("""() => {
        window.fixture.additionalShelves=[{title:'Live performances',items:[{kind:'track',video_id:'abcdefghijk',title:'Live song'}]},{title:'From your library',items:[{kind:'album',browseId:'MPRElibrary',title:'Library album'}]}];
        window.playFromQueue=item=>window.played=item.video_id;
        return window.loadArtist('UCtest');
    }""")
    expect(page.locator('#artist-extra-shelves .hscroll-section')).to_have_count(2)
    page.locator('#artist-extra-0 .hscroll-card').click()
    assert page.evaluate('window.played') == 'abcdefghijk'
    page.locator('#artist-extra-1 .hscroll-card-title').click()
    assert page.evaluate('window._route') == '#album/MPRElibrary'
