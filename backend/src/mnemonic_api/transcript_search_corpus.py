"""Repeatable, streaming reads of a transcript scope inside one database snapshot."""

from collections.abc import Iterator, Sequence
from uuid import UUID

from sqlalchemy import Uuid, any_, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session, defer, undefer
from sqlalchemy.sql import Select

from mnemonic_api.models import Transcript


class TranscriptCorpus:
    def __init__(self, database: Session, statement: Select) -> None:
        self.database = database
        self.statement = statement

    @property
    def ids(self):
        return self.statement.with_only_columns(Transcript.id)

    def __len__(self) -> int:
        return self.database.scalar(select(func.count()).select_from(self.ids.subquery())) or 0

    def __iter__(self) -> Iterator[Transcript]:
        return self.records()

    def records(self, *, include_text: bool = False) -> Iterator[Transcript]:
        option = undefer if include_text else defer
        rows = self.database.scalars(self.statement.options(option(Transcript.normalized_text))
            .order_by(Transcript.id).execution_options(yield_per=1))
        try:
            yield from rows
        finally:
            rows.close()

    def matching(self, identities: Sequence[UUID]) -> TranscriptCorpus:
        # One array parameter also supports libraries beyond PostgreSQL's bind limit.
        return TranscriptCorpus(self.database, self.statement.where(
            Transcript.id == any_(cast(list(identities), ARRAY(Uuid)))))
