"""Comandi di amministrazione: `secondbrain <gruppo> <azione>` (spec §5).

Ogni gruppo di comandi è una funzione `_add_*_commands(subparsers)` elencata in
COMMAND_GROUPS; ogni azione riceve gli argomenti e solleva CliError in caso di errore.
"""
import argparse
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from sqlalchemy.orm import Session

from . import catalog, devices
from .clock import utcnow
from .config import ConfigError, Settings, load_settings
from .naming import DEFAULT_DEVICE_TYPE


class CliError(Exception):
    """Errore da mostrare all'utente, senza traceback."""


@contextmanager
def open_session() -> Iterator[tuple[Session, Settings]]:
    settings = load_settings()
    engine = catalog.make_engine(settings.database_url)
    try:
        with catalog.make_sessionmaker(engine)() as session:
            yield session, settings
    finally:
        engine.dispose()


def _device_add(args: argparse.Namespace) -> None:
    with open_session() as (s, _):
        try:
            device, token = devices.create_device(s, args.id, args.name or args.id,
                                                  args.type, utcnow())
        except devices.DeviceError as exc:
            raise CliError(str(exc)) from None
        s.commit()
    print(f"Dispositivo {device.id} ({device.name}) registrato.")
    print(f"Token (mostrato solo ora): {token}")


def _device_token(args: argparse.Namespace) -> None:
    with open_session() as (s, _):
        try:
            token = devices.regenerate_token(s, args.id)
        except devices.DeviceError as exc:
            raise CliError(str(exc)) from None
        s.commit()
    print(f"Nuovo token per {args.id}; il precedente non vale più.")
    print(f"Token (mostrato solo ora): {token}")


def _device_list(args: argparse.Namespace) -> None:
    with open_session() as (s, _):
        for d in catalog.list_devices(s):
            seen = d.last_seen_at.isoformat(timespec="seconds") if d.last_seen_at else "mai"
            print(f"{d.id}  {d.name}  tipo={d.type}  token={'sì' if d.token_hash else 'no'}"
                  f"  ultimo contatto={seen}  firmware={d.last_firmware or '-'}")


def _add_device_commands(sub) -> None:
    group = sub.add_parser("device", help="dispositivi e token")
    actions = group.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="registra un dispositivo e genera il token")
    add.add_argument("id", help="MAC del dispositivo, 12 cifre esadecimali minuscole")
    add.add_argument("--name", help="nome leggibile")
    add.add_argument("--type", default=DEFAULT_DEVICE_TYPE, help="tipo di dispositivo")
    add.set_defaults(func=_device_add)
    token = actions.add_parser("token", help="nuovo token (il precedente smette di valere)")
    token.add_argument("id")
    token.set_defaults(func=_device_token)
    actions.add_parser("list", help="elenca i dispositivi").set_defaults(func=_device_list)


COMMAND_GROUPS: tuple[Callable, ...] = (_add_device_commands,)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="secondbrain")
    sub = parser.add_subparsers(dest="command", required=True)
    for add_group in COMMAND_GROUPS:
        add_group(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (CliError, ConfigError) as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
