import os
import sys

import pytest


SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

import server  # noqa: E402


SAMPLE_HOME = [
    {
        "title": "Made for You",
        "contents": [
            {
                "title": "Song Title",
                "videoId": "abc123",
                "artists": [{"name": "Artist Name"}],
                "thumbnails": [
                    {
                        "url": "https://example.com/thumb.jpg",
                        "width": 120,
                        "height": 120,
                    }
                ],
            },
            {
                "title": "Album Only",
                "videoId": None,
                "artists": [{"name": "Some Artist"}],
                "thumbnails": [{"url": "https://example.com/album.jpg"}],
            },
        ],
    },
    {
        "title": "Quick Picks",
        "subtitle": "Based on your recent listens",
        "contents": [
            {
                "title": "Another Song",
                "videoId": "def456",
                "artists": [{"name": "Another Artist"}],
                "thumbnails": [{"url": "https://example.com/thumb2.jpg"}],
            }
        ],
    },
]


class FakeYTMusic:
    calls = 0

    def get_home(self, limit=5):
        FakeYTMusic.calls += 1
        return SAMPLE_HOME


class RaisingYTMusic:
    def get_home(self, limit=5):
        raise RuntimeError("raw upstream details")


@pytest.fixture(autouse=True)
def reset_home_state(monkeypatch):
    if hasattr(server, "_home_cache"):
        server._home_cache["built_at"] = 0
        server._home_cache["rows"] = []
    server._recs_cache["built_at"] = 0
    server._recs_cache["items"] = []
    FakeYTMusic.calls = 0
    monkeypatch.setattr(server, "YTMusic", FakeYTMusic)


@pytest.fixture
def client():
    server.app.config.update(TESTING=True)
    return server.app.test_client()


def get_home(client):
    return client.get("/api/home/", headers={"X-Api-Key": server.API_KEY})


def test_home_endpoint_returns_rows(client):
    response = get_home(client)

    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data["rows"], list)


def test_home_has_items(client):
    data = get_home(client).get_json()

    assert data["rows"][0]["items"]


def test_row_has_title(client):
    data = get_home(client).get_json()

    for row in data["rows"]:
        assert isinstance(row["title"], str)
        assert row["title"]


def test_item_shape(client):
    data = get_home(client).get_json()

    for row in data["rows"]:
        for item in row["items"]:
            assert set(item) == {"videoId", "title", "artist", "thumbnail"}
            assert all(isinstance(item[key], str) for key in item)


def test_non_video_items_filtered(client):
    data = get_home(client).get_json()
    titles = [item["title"] for row in data["rows"] for item in row["items"]]

    assert "Album Only" not in titles


def test_home_fallback(client, monkeypatch):
    server._recs_cache["items"] = [
        {
            "videoId": "fallback123",
            "title": "Fallback Song",
            "artist": "Fallback Artist",
            "thumbnail": "https://example.com/fallback.jpg",
        }
    ]
    monkeypatch.setattr(server, "YTMusic", RaisingYTMusic)

    response = get_home(client)

    assert response.status_code == 200
    data = response.get_json()
    assert data == {"rows": [{"title": "Recommended", "items": server._recs_cache["items"]}]}
    assert "raw upstream details" not in response.get_data(as_text=True)


def test_cache_hit(client):
    first = get_home(client).get_json()
    second = get_home(client).get_json()

    assert second == first
    assert FakeYTMusic.calls == 1
