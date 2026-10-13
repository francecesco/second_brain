from secondbrain import ha_store
from secondbrain import homeassistant as ha
from secondbrain import settings_store as store
from tests.helpers import NOW, TEST_SETTINGS_KEY


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def configure(db):
    ha_store.save_connection(db, store.SecretBox(TEST_SETTINGS_KEY), "http://ha.test:8123", "token-finto")
    ha_store.save_reading(db, ha.Reading((), (), (), ()), NOW)
    db.commit()


def test_digest_requires_token(client):
    assert client.get("/digest/today").status_code == 401


def test_digest_without_config_is_204(client, token):
    r = client.get("/digest/today", headers=auth(token))
    assert r.status_code == 204 and r.content == b""


def test_digest_returns_packed_image_with_etag(client, token, db):
    configure(db)
    r = client.get("/digest/today", headers=auth(token))
    assert r.status_code == 200 and len(r.content) == 3400
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["etag"]


def test_digest_if_none_match_is_304(client, token, db):
    configure(db)
    tag = client.get("/digest/today", headers=auth(token)).headers["etag"]
    for value in (tag, "W/" + tag, '"altro", ' + tag):
        r = client.get("/digest/today", headers={**auth(token), "If-None-Match": value})
        assert r.status_code == 304 and r.content == b""
    r = client.get("/digest/today", headers={**auth(token), "If-None-Match": '"altro"'})
    assert r.status_code == 200


def test_preview_requires_login(client):
    r = client.get("/settings/homeassistant/preview.png", follow_redirects=False)
    assert r.status_code in (302, 303) and "/login" in r.headers["location"]


def test_preview_png(ui, db):
    assert ui.client.get("/settings/homeassistant/preview.png").status_code == 404
    configure(db)
    r = ui.client.get("/settings/homeassistant/preview.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")
