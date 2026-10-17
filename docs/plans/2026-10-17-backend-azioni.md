# Piano — Azioni e scadenze (spec `docs/specs/2026-10-17-backend-azioni-design.md`)

Branch `backend-azioni`. Ogni task: prima i test che falliscono, poi il codice,
`uv run pytest -q` verde prima del commit.

1. **Modello puro** (`notefile.py`): `Action`, `Note.actions`, render/parse del frontmatter
   (`id` deterministico se manca), pulizia (una riga, 200 caratteri, niente doppioni, max 10),
   `apply_enrichment`/`apply_actions` che rispettano `edited`, modifica e spunta a mano,
   modifiche fatte in Obsidian (`merge_edits`, `_merge_unseen_edit`).
2. **Catalogo**: colonna `captures.actions` JSONB (migrazione `0007`), `note_from_capture`,
   `note_fields`, `empty_ai_fields`; `rescan` la ricostruisce dal `.md`.
3. **AI**: prompt con la chiave `actions` e il contesto (data e giorno della registrazione,
   eventi dei calendari nei 14 giorni dall'istantanea di Home Assistant), schema di Gemini,
   `validate_enrichment` tollerante (chiave mancante = nessuna azione), date assurde scartate
   rispetto al giorno della registrazione.
4. **Fase `actions`**: `jobs.enqueue_actions`, worker che rifà solo le azioni, CLI
   `secondbrain actions --all|<id>`, pulsante nelle Impostazioni.
5. **Spunte**: `library.set_action_done` e `edit_actions` con le regole nota/azioni (tutte
   fatte → nota completata; una riaperta → nota di nuovo da fare; spunta della nota → tutte
   fatte; tolta → tutte riaperte se erano tutte fatte).
6. **UI**: azioni sotto la riga, route di spunta con aggiornamenti out-of-band, sezione In
   scadenza, tessere nuove, campo "Azioni" modificabile nel dettaglio, telefono; `stile-ui.md`.
7. **Digest**: righe delle azioni scadute e di oggi, immagine anche senza Home Assistant.
8. **Verifica**: test completi, screenshot a 1440 e 390 px in chiaro e scuro, prova di
   interazione nel browser; stato della spec aggiornato.
