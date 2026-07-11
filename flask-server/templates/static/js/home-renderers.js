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

    const renderItem = (item, layout) => {
        if (!item) return '';
        const title = escapeHtml(item.title || 'Unknown');
        const subtitle = escapeHtml(item.subtitle || '');
        const image = getImageUrl(item);
        
        const play = item.play || {};
        const cap = item.capabilities || {};
        const videoId = play.videoId ? escapeHtml(play.videoId) : '';
        const playlistId = play.playlistId ? escapeHtml(play.playlistId) : '';
        const kind = escapeHtml(item.kind || 'unknown');
        const targetId = item.target ? escapeHtml(item.target.id || '') : '';
        
        const dataAttrs = `data-kind="${kind}" data-video-id="${videoId}" data-playlist-id="${playlistId}" data-target-id="${targetId}"`;
        
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
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M15.41 16.59L10.83 12l4.58-4.59L14 6l-6 6 6 6 1.41-1.41z"/></svg>
                </button>
                <button class="home-scroll-btn home-scroll-right" aria-label="Scroll right">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z"/></svg>
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

    return {
        escapeHtml,
        renderItem,
        renderShelf,
        extractPlayQueue,
        filterShelves
    };
}));
