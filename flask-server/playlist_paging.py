"""Bounded bulk playback fetches avoid repeatedly traversing YouTube continuation prefixes."""
def playlist_page_request(args):
    bulk = args.get('playback') == '1'
    cap = 5000 if bulk else 100
    limit = min(cap, max(1, int(args.get('limit', cap if bulk else 30) or 30)))
    offset = max(0, int(args.get('offset', 0) or 0))
    if bulk and offset:
        raise ValueError('Playback queues must start at offset zero')
    return limit, offset
