"""Foundation infrastructure table.

Not a domain entity — a small key/value marker that gives ``create_all()`` a real
table to build and lets the DB round-trip be smoke-tested. Domain tables (Drawing,
Page, Prompt, ...) are introduced by their own tickets.
"""

from sqlmodel import Field, SQLModel


class AppMeta(SQLModel, table=True):
    __tablename__ = "app_meta"

    key: str = Field(primary_key=True)
    value: str
