"""Provider conosciuti con i default dei modelli (spec §4) e la configurazione in uso.

Default verificati sulla documentazione dei provider il 2026-09-29; si cambiano dalla
pagina impostazioni e si controllano col pulsante "Prova".
"""
from dataclasses import dataclass, field

KIND_OPENAI_COMPATIBLE = "openai_compatible"
KIND_GEMINI = "gemini"
MB = 1000 * 1000  # i provider dichiarano i limiti in MB decimali: così si resta sotto
GROQ_MAX_AUDIO_BYTES = 25 * MB
OPENAI_MAX_AUDIO_BYTES = 25 * MB
GEMINI_MAX_REQUEST_BYTES = 20 * MB  # richiesta inline intera, audio in base64 compreso


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str
    kind: str
    base_url: str
    transcribe_model: str
    text_model: str
    audio_formats: tuple[str, ...]  # formati di ai.audio, in ordine di preferenza
    max_bytes: int                  # file audio (OpenAI compatibile) o richiesta intera (Gemini)


PROVIDERS = (
    ProviderSpec("groq", "Groq", KIND_OPENAI_COMPATIBLE, "https://api.groq.com/openai/v1",
                 "whisper-large-v3-turbo", "openai/gpt-oss-120b", ("flac", "wav"),
                 GROQ_MAX_AUDIO_BYTES),
    ProviderSpec("gemini", "Gemini", KIND_GEMINI,
                 "https://generativelanguage.googleapis.com/v1beta",
                 "gemini-3.8-flash", "gemini-3.8-flash", ("flac", "wav"),
                 GEMINI_MAX_REQUEST_BYTES),
    # FLAC non confermato per OpenAI: si manda il WAV. Modello di testo da scegliere.
    ProviderSpec("openai", "OpenAI", KIND_OPENAI_COMPATIBLE, "https://api.openai.com/v1",
                 "gpt-4o-mini-transcribe", "", ("wav",), OPENAI_MAX_AUDIO_BYTES),
)
PROVIDER_SPECS = {spec.name: spec for spec in PROVIDERS}


@dataclass(frozen=True)
class ProviderConfig:
    """Provider pronto all'uso: abilitato, con la chiave decifrata (mai nel repr)."""
    name: str
    transcribe_model: str
    text_model: str
    api_key: str = field(repr=False)

    @property
    def spec(self) -> ProviderSpec:
        return PROVIDER_SPECS[self.name]
