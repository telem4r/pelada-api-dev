"""Guest identity in finance, using the existing UUID/text storage contract.

Reads never change money or guess which homonym paid. New payment notes carry
the immutable guest UUID and a lossless name snapshot; older formats remain
readable. This module performs no database or network operations.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import re
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class GuestReference:
    guest_id: str | None = None
    name: str | None = None
    present: bool = False


def _name(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def guest_reference(value: Any) -> GuestReference:
    raw = _name(value) or ''
    if not raw.startswith('guest_id:'):
        return GuestReference()
    identifier, separator, suffix = raw[len('guest_id:'):].partition(';')
    try:
        identifier = str(UUID(identifier))
    except (ValueError, AttributeError):
        identifier = None
    name = None
    if separator:
        if suffix.startswith('guest_name_json:'):
            try:
                decoded, _ = json.JSONDecoder().raw_decode(suffix[len('guest_name_json:'):])
                name = _name(decoded)
            except (ValueError, TypeError):
                pass
        elif suffix.startswith('guest_name:'):
            name = _name(suffix[len('guest_name:'):].rsplit(';match_id:', 1)[0])
        elif suffix.startswith('Multa falta - '):
            name = _name(suffix[len('Multa falta - '):])
        else:
            name = _name(suffix)
    return GuestReference(identifier, name, True)


def guest_payment_notes(guest_id: str, name: str, annotation: str | None = None) -> str:
    identifier = str(UUID(str(guest_id)))
    result = f'guest_id:{identifier};guest_name_json:{json.dumps(_name(name) or "Convidado", ensure_ascii=False)}'
    if annotation:
        result += f';note_json:{json.dumps(annotation, ensure_ascii=False)}'
    return result


def guest_title_name(value: Any) -> str | None:
    raw = _name(value) or ''
    prefix = 'Partida - convidado '
    if raw.startswith(prefix):
        return _name(raw[len(prefix):])
    # Format used by older app versions, not a general free-text heuristic.
    match = re.fullmatch(r'Convidado (.+) - partida #\d+', raw)
    return _name(match.group(1)) if match else None


def is_guest_finance_row(row: dict[str, Any]) -> bool:
    source = row.get('obligation_source_type') or row.get('source_type')
    if source == 'convidado_partida' or row.get('category') in {'single_guest', 'guest_single', 'convidado_partida'}:
        return True
    return (
        not row.get('player_id') and not row.get('user_id')
        and row.get('category') in {'fine', 'multa'}
        and guest_reference(row.get('notes')).present
    )


class GuestFinanceDirectory:
    def __init__(self, guests: list[dict[str, Any]]) -> None:
        self.by_id: dict[tuple[str, str], dict] = {}
        self.by_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for guest in guests:
            match_id = str(guest['match_id'])
            identifier = str(guest['guest_id'])
            name = _name(guest.get('name')) or 'Convidado'
            item = {'guest_id': identifier, 'name': name}
            self.by_id[(match_id, identifier)] = item
            self.by_name[(match_id, name)].append(item)

    def enrich(self, row: dict[str, Any]) -> dict[str, Any]:
        if not is_guest_finance_row(row):
            return row
        row = dict(row)
        match_id = str(row.get('match_id') or row.get('obligation_match_id') or '')
        notes_ref = guest_reference(row.get('notes'))
        obligation_ref = guest_reference(row.get('obligation_description') or row.get('description'))
        title_name = guest_title_name(row.get('obligation_title') or row.get('title'))
        explicit = obligation_ref if obligation_ref.present else notes_ref
        conflict = bool(
            obligation_ref.guest_id and notes_ref.guest_id
            and obligation_ref.guest_id != notes_ref.guest_id
        )
        identifier = explicit.guest_id
        # Saved snapshots survive guest deletion/renaming. Conflicting notes
        # must not identify somebody else as the subject of an obligation.
        name = title_name if conflict else (notes_ref.name or title_name)
        status = 'reference' if identifier else 'unresolved'
        if conflict:
            identifier = None
            status = 'conflict'
        elif identifier:
            guest = self.by_id.get((match_id, identifier))
            name = name or (guest['name'] if guest else None)
        elif explicit.present:
            status = 'invalid_reference'
        else:
            # The legacy writer saved only a name. Even a unique current
            # name cannot prove historical identity after removal/re-entry.
            # Record the evidence, but never invent a UUID from a name.
            name = name or _name(row.get('notes'))
            matches = self.by_name.get((match_id, name), [])
            if len(matches) == 1:
                status = 'legacy_name'
            elif len(matches) > 1:
                status = 'ambiguous'
        row['guest_id'] = identifier
        row['guest_name'] = name or 'Convidado'
        row['guest_identity_status'] = status
        row['player_name'] = row['guest_name']
        row['user_name'] = row['guest_name']
        return row
