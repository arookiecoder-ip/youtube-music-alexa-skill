from dataclasses import dataclass
from typing import List, Optional
from enum import Enum

@dataclass
class Thumbnail:
    url: str
    width: int
    height: int
    
@dataclass
class Metadata:
    title: str
    artist: str
    video_id: str
    thumbnail: Optional[Thumbnail]
    duration_ms: int = 0

@dataclass
class Stream:
    audio_url: str

@dataclass
class SongInfo:
    metadata: Metadata
    stream: Stream

@dataclass
class SongInfoList:
    song_info: SongInfo
    playlist: List[Metadata]
    queue_id: Optional[str] = None
    next_offset: int = 0

@dataclass
class Playlist:
    id: str
    title: str

class Filter(Enum):
    SONGS = 'songs'
    ARTISTS = 'artists'
    ALBUMS = 'albums'
