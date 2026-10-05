"""Guard input management (PRD 7.3). Guards are inputs the ruleset must NOT
answer; they rotate by human curation, not automatic bulk-adding."""

from __future__ import annotations

from ..storage.versions import VersionStore


def list_guards(store: VersionStore) -> list[str]:
    return store.load_guards()


def add_guard(store: VersionStore, text: str) -> bool:
    guards = store.load_guards()
    if text in guards:
        return False
    guards.append(text)
    store.save_guards(guards)
    return True
