import pytest

from secondbrain.cli import main
from secondbrain.devices import hash_token
from secondbrain.models import Device
from tests.helpers import DEV, TEST_DB


@pytest.fixture
def cli_db(monkeypatch, db):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    return db


def last_value(out: str) -> str:
    return out.strip().splitlines()[-1].split(": ", 1)[1]


def test_device_add_prints_token(cli_db, capsys):
    assert main(["device", "add", DEV, "--name", "e-paper"]) == 0
    token = last_value(capsys.readouterr().out)
    assert cli_db.get(Device, DEV).token_hash == hash_token(token)


def test_device_add_duplicate_fails(cli_db, capsys):
    main(["device", "add", DEV])
    assert main(["device", "add", DEV]) == 1
    assert "già registrato" in capsys.readouterr().err


def test_device_token_and_list(cli_db, capsys):
    main(["device", "add", DEV, "--name", "e-paper"])
    assert main(["device", "token", DEV]) == 0
    token = last_value(capsys.readouterr().out)
    assert cli_db.get(Device, DEV).token_hash == hash_token(token)
    assert main(["device", "list"]) == 0
    out = capsys.readouterr().out
    assert "e-paper" in out and "token=sì" in out


def test_missing_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["device", "list"]) == 1
    assert "DATABASE_URL" in capsys.readouterr().err
