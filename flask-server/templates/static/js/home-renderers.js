(function (root, factory) {
    if (typeof define === 'function' && define.amd) {
        define([], factory);
    } else if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.HomeRenderers = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    const escapeHtml = (unsafe) => {
        if (unsafe == null) return '';
        return String(unsafe)
             .replace(/&/g, "&amp;")
             .replace(/</g, "&lt;")
             .replace(/>/g, "&gt;")
             .replace(/"/g, "&quot;")
             .replace(/'/g, "&#039;");
    };

    const getImageUrl = (item) => {
        if (item.image) return escapeHtml(item.image);
        if (item.images && item.images.length > 0) {
            return escapeHtml(item.images[item.images.length - 1].url);
        }
        return ''; // Fallback
    };

    const renderSubtitle = (item) => {
        const artists = Array.isArray(item.artists) ? item.artists.filter(a => a && a.name) : [];
        if (!artists.length) return escapeHtml(item.subtitle || '');
        const artistHtml = artists.map(a => {
            const id = a.id ? ` data-channel-id="${escapeHtml(a.id)}"` : '';
            return `<span class="artist-name" data-artist-name="${escapeHtml(a.name)}"${id}>${escapeHtml(a.name)}</span>`;
        }).join(', ');
        const album = item.album ? ` • ${escapeHtml(item.album)}` : '';
        return artistHtml + album;
    };

    const renderItem = (item, layout) => {
        if (!item) return '';
        const title = escapeHtml(item.title || 'Unknown');
        const subtitle = renderSubtitle(item);
        const image = getImageUrl(item);
        
        const play = item.play || {};
        const cap = item.capabilities || {};
        const videoId = play.videoId ? escapeHtml(play.videoId) : '';
        const playlistId = play.playlistId ? escapeHtml(play.playlistId) : '';
        const kind = escapeHtml(item.kind || 'unknown');
        // Keep the browse id on the card even when a target was omitted by a
        // shelf provider.  Track cards use the album id for title navigation.
        const targetId = item.target ? (item.target.id || '') :
            (item.browseId || item.playlistId || item.targetId || '');
        
        const albumId = item.albumId || item.album_id ||
            (item.album && typeof item.album === 'object' && (item.album.id || item.album.browseId)) || '';
        const artistId = item.channelId || item.channel_id || item.artistId || item.artist_id ||
            (Array.isArray(item.artists) && item.artists[0] &&
                (item.artists[0].id || item.artists[0].browseId || item.artists[0].channelId)) || '';
        const dataAttrs = `data-kind="${kind}" data-video-id="${videoId}" data-playlist-id="${playlistId}" data-target-id="${escapeHtml(targetId)}" data-album-id="${escapeHtml(albumId)}" data-channel-id="${escapeHtml(artistId)}"`;
        
        let playBtnHtml = '';
        if (cap.play) {
            playBtnHtml = `<button class="home-play-btn" aria-label="Play ${title}"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="7,4 20,12 7,20"/></svg></button>`;
        }
        
        if (layout === 'shortcuts') {
            return `
                <div class="home-item home-item-shortcut" ${dataAttrs}>
                    <img src="${image}" alt="${title}" class="home-item-img" loading="lazy">
                    <div class="home-item-text">
                        <div class="home-item-title">${title}</div>
                    </div>
                    ${playBtnHtml}
                </div>
            `;
        } else if (layout === 'song_grid') {
            return `
                <div class="home-item home-item-song" ${dataAttrs}>
                    <img src="${image}" alt="${title}" class="home-item-img" loading="lazy">
                    <div class="home-item-text">
                        <div class="home-item-title">${title}</div>
                        <div class="home-item-subtitle">${subtitle}</div>
                    </div>
                    ${playBtnHtml}
                </div>
            `;
        } else if (layout === 'cards' || layout === 'wide_cards') {
            const cardClass = layout === 'wide_cards' ? 'home-item-wide-card' : 'home-item-card';
            return `
                <div class="home-item ${cardClass}" ${dataAttrs}>
                    <div class="home-item-img-wrapper">
                        <img src="${image}" alt="${title}" class="home-item-img" loading="lazy">
                        ${playBtnHtml}
                    </div>
                    <div class="home-item-text">
                        <div class="home-item-title">${title}</div>
                        <div class="home-item-subtitle">${subtitle}</div>
                    </div>
                </div>
            `;
        } else if (layout === 'circles') {
            return `
                <div class="home-item home-item-circle" ${dataAttrs}>
                    <div class="home-item-img-wrapper">
                        <img src="${image}" alt="${title}" class="home-item-img" loading="lazy">
                        ${playBtnHtml}
                    </div>
                    <div class="home-item-text">
                        <div class="home-item-title">${title}</div>
                        <div class="home-item-subtitle">${subtitle}</div>
                    </div>
                </div>
            `;
        } else {
            return `<div class="home-item home-item-fallback" ${dataAttrs}>${title}</div>`;
        }
    };

    const renderShelf = (shelf) => {
        if (!shelf || !shelf.items || shelf.items.length === 0) return '';
        const title = escapeHtml(shelf.title || '');
        const subtitle = escapeHtml(shelf.subtitle || '');
        const layout = escapeHtml(shelf.layout || 'song_grid');
        
        let itemsHtml = shelf.items.map(i => renderItem(i, layout)).join('');
        
        let playAllHtml = '';
        if (shelf.actions && shelf.actions.playAll) {
            playAllHtml = `<button class="home-shelf-play-all" data-shelf-id="${escapeHtml(shelf.id)}">Play all</button>`;
        }
        
        let scrollBtnsHtml = `
            <div class="home-shelf-scroll-btns">
                <button class="home-scroll-btn home-scroll-left" aria-label="Scroll left">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
                </button>
                <button class="home-scroll-btn home-scroll-right" aria-label="Scroll right">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
                </button>
            </div>
        `;
        
        return `
            <div class="home-shelf home-layout-${layout}" data-shelf-id="${escapeHtml(shelf.id)}" data-layout="${layout}">
                <div class="home-shelf-header">
                    <div class="home-shelf-title-area">
                        ${subtitle ? `<div class="home-shelf-subtitle">${subtitle}</div>` : ''}
                        <h2 class="home-shelf-title">${title}</h2>
                    </div>
                    <div class="home-shelf-actions">
                        ${playAllHtml}
                        ${scrollBtnsHtml}
                    </div>
                </div>
                <div class="home-shelf-content">
                    ${itemsHtml}
                </div>
            </div>
        `;
    };

    const extractPlayQueue = (shelf) => {
        if (!shelf || !shelf.items) return [];
        return shelf.items.filter(i => i.capabilities && i.capabilities.play && i.play && i.play.videoId).map(i => i.play.videoId);
    };
    
    const filterShelves = (feed, filterId) => {
        if (!feed || !feed.shelves) return [];
        if (filterId === 'all') return feed.shelves;
        return feed.shelves.filter(s => s.filters && s.filters.includes(filterId));
    };

    // Expose as a global so playlists.js / explore.js can call escapeHtml() directly
    if (typeof window !== 'undefined') window.escapeHtml = escapeHtml;

    return {
        escapeHtml,
        renderItem,
        renderShelf,
        extractPlayQueue,
        filterShelves
    };
}));
