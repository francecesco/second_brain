"""Catena dei provider (spec §5): il principale, poi le riserve nell'ordine delle impostazioni.

Ogni fase percorre la catena da capo: chi ha trascritto non vincola chi arricchisce.
Un ServiceError fa passare al successivo; un ContentError ferma tutto (la nota fallisce).
"""
import logging
from collections.abc import Callable, Sequence
from typing import TypeVar

from .audio import AudioSource
from .base import ContentError, Enrichment, Provider, ServiceError, Transcript
from .registry import ProviderConfig

log = logging.getLogger(__name__)

ProviderFactory = Callable[[ProviderConfig], Provider]
UsageCallback = Callable[[str, float], None]  # (provider, secondi di audio trascritti)
T = TypeVar("T")


class AllProvidersFailed(Exception):
    """Nessun provider ha risposto: il lavoro torna in coda con backoff."""

    def __init__(self, errors: Sequence[ServiceError]):
        self.errors = list(errors)
        super().__init__("; ".join(str(e) for e in self.errors)
                         or "nessun provider utilizzabile per questa fase")


def _run(configs: Sequence[ProviderConfig], factory: ProviderFactory,
         has_model: Callable[[ProviderConfig], str], call: Callable[[Provider], T],
         on_call: UsageCallback, audio_seconds: float) -> tuple[ProviderConfig, T]:
    errors: list[ServiceError] = []
    for config in configs:
        if not has_model(config):
            continue
        provider = factory(config)
        try:
            result = call(provider)
        except ServiceError as exc:
            on_call(config.name, 0.0)
            log.warning("%s non disponibile, provo il successivo: %s", config.name, exc.detail)
            errors.append(exc)
            continue
        except ContentError:
            on_call(config.name, 0.0)
            raise
        on_call(config.name, audio_seconds)
        return config, result
    raise AllProvidersFailed(errors)


def transcribe(configs: Sequence[ProviderConfig], factory: ProviderFactory, audio: AudioSource,
               language: str, on_call: UsageCallback) -> tuple[ProviderConfig, Transcript]:
    seconds = audio.duration_s  # AudioError qui, prima di disturbare qualunque provider
    return _run(configs, factory, lambda c: c.transcribe_model,
                lambda p: p.transcribe(audio, language), on_call, seconds)


def enrich(configs: Sequence[ProviderConfig], factory: ProviderFactory, text: str,
           language: str, on_call: UsageCallback) -> tuple[ProviderConfig, Enrichment]:
    return _run(configs, factory, lambda c: c.text_model,
                lambda p: p.enrich(text, language), on_call, 0.0)
