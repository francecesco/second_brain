import pytest
from cryptography.fernet import Fernet

from secondbrain import settings_store as store
from secondbrain.cli import main
from secondbrain.models import AiProvider
from tests.helpers import NOW, TEST_SETTINGS_KEY

GROQ_KEY = "gsk_test_groq_0123456789abcdef"


@pytest.fixture
def box():
    return store.SecretBox(TEST_SETTINGS_KEY)


def save(db, box, name, api_key, enabled=True, **models):
    spec_models = {"transcribe_model": "m-stt", "text_model": "m-llm"} | models
    return store.save_provider(db, box, name, api_key=api_key, enabled=enabled, now=NOW,
                               **spec_models)


def test_secret_box_round_trip_and_wrong_key(box):
    token = box.encrypt(GROQ_KEY)
    assert GROQ_KEY not in token and box.decrypt(token) == GROQ_KEY
    assert store.SecretBox(Fernet.generate_key().decode()).decrypt(token) is None
    assert box.decrypt("non-un-token") is None


def test_secret_box_without_key():
    box = store.SecretBox(None)
    assert not box.available and box.decrypt("qualcosa") is None
    with pytest.raises(store.NoSettingsKey):
        box.encrypt(GROQ_KEY)


def test_mask_key():
    assert store.mask_key("gsk_test_abcdefghijklmnopa3f") == "gsk_…a3f"
    assert store.mask_key("corta") == "…"


def test_generate_key_and_cli(capsys):
    Fernet(store.generate_key().encode())
    assert main(["gen-key"]) == 0
    Fernet(capsys.readouterr().out.strip().encode())


def test_default_providers(db):
    rows = store.ensure_providers(db, NOW)
    assert [(r.name, r.transcribe_model, r.text_model, r.enabled, r.api_key_enc) for r in rows] == [
        ("groq", "whisper-large-v3-turbo", "openai/gpt-oss-120b", True, None),
        ("gemini", "gemini-3.8-flash", "gemini-3.8-flash", True, None),
        ("openai", "gpt-4o-mini-transcribe", "", True, None),
    ]
    assert store.ensure_providers(db, NOW) == rows


def test_saved_key_is_encrypted_and_masked(db, box):
    store.ensure_providers(db, NOW)
    save(db, box, "groq", f"  {GROQ_KEY} ", transcribe_model=" whisper-large-v3 ")
    db.commit()
    row = db.get(AiProvider, "groq")
    assert GROQ_KEY not in row.api_key_enc and box.decrypt(row.api_key_enc) == GROQ_KEY
    assert row.transcribe_model == "whisper-large-v3"
    assert store.key_status(row, box) == "gsk_…def"
    save(db, box, "groq", "")  # campo vuoto: la chiave resta quella
    assert box.decrypt(db.get(AiProvider, "groq").api_key_enc) == GROQ_KEY
    store.clear_api_key(db, "groq", NOW)
    assert store.key_status(db.get(AiProvider, "groq"), box) == store.NO_KEY


def test_saving_a_key_needs_settings_key(db):
    store.ensure_providers(db, NOW)
    with pytest.raises(store.NoSettingsKey):
        save(db, store.SecretBox(None), "groq", GROQ_KEY)
    save(db, store.SecretBox(None), "groq", "", transcribe_model="altro")
    assert db.get(AiProvider, "groq").transcribe_model == "altro"


def test_provider_configs_skip_disabled_missing_and_unreadable(db, box):
    store.ensure_providers(db, NOW)
    save(db, box, "groq", GROQ_KEY)
    save(db, box, "gemini", "AIza-test-gemini-0123456789", enabled=False)
    db.get(AiProvider, "openai").api_key_enc = store.SecretBox(
        Fernet.generate_key().decode()).encrypt("sk-test-openai-0123456789")
    db.commit()
    configs = store.provider_configs(db, box)
    assert [(c.name, c.api_key, c.transcribe_model) for c in configs] == [("groq", GROQ_KEY, "m-stt")]
    assert GROQ_KEY not in repr(configs[0])
    assert store.key_status(db.get(AiProvider, "openai"), box) == store.UNREADABLE_KEY
    assert store.provider_configs(db, store.SecretBox(None)) == []


def test_has_configured_provider(db, box):
    store.ensure_providers(db, NOW)
    assert not store.has_configured_provider(db)
    save(db, box, "gemini", "AIza-test-gemini-0123456789", enabled=False)
    assert not store.has_configured_provider(db)
    save(db, box, "gemini", "", enabled=True)
    assert store.has_configured_provider(db)


def test_move_provider(db):
    store.ensure_providers(db, NOW)
    store.move_provider(db, "gemini", store.MOVE_UP, NOW)
    assert [r.name for r in store.list_providers(db)] == ["gemini", "groq", "openai"]
    store.move_provider(db, "gemini", store.MOVE_UP, NOW)
    store.move_provider(db, "openai", store.MOVE_DOWN, NOW)
    assert [r.name for r in store.list_providers(db)] == ["gemini", "groq", "openai"]
    with pytest.raises(store.UnknownProvider):
        store.move_provider(db, "acme", store.MOVE_UP, NOW)


def test_unknown_provider(db, box):
    store.ensure_providers(db, NOW)
    with pytest.raises(store.UnknownProvider):
        save(db, box, "acme", GROQ_KEY)


def test_pause_and_language(db):
    assert (store.is_paused(db), store.get_language(db)) == (False, "it")
    store.set_paused(db, True)
    store.set_language(db, "en")
    db.commit()
    assert (store.is_paused(db), store.get_language(db)) == (True, "en")
    with pytest.raises(ValueError):
        store.set_language(db, "xx")
