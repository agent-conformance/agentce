"""The evidence graph store (ADR-0001): an embedded, on-disk SQLite database.

The provenance graph is held as triples in relational tables -- object-property edges, datatype
literals, and the transitive ``rdfs:subClassOf*`` closure -- written once during graph building and
queried read-mostly during evaluation. Every query orders explicitly, so results never depend on
physical row order (HR-1 determinism). PSP shapes compile to SQL over these tables (item 1.7).

IRIs are stored as compact CURIEs (``agentce:``, ``prov:``, ``rdf:type``) rather than expanded URLs,
which keeps the tables small and comparisons exact.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

RDF_TYPE = "rdf:type"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS edges (
    s TEXT NOT NULL, p TEXT NOT NULL, o TEXT NOT NULL,
    PRIMARY KEY (s, p, o)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS edges_po ON edges (p, o);
CREATE INDEX IF NOT EXISTS edges_sp ON edges (s, p);
CREATE TABLE IF NOT EXISTS literals (
    s TEXT NOT NULL, p TEXT NOT NULL, val TEXT NOT NULL, datatype TEXT NOT NULL,
    PRIMARY KEY (s, p, val, datatype)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS literals_sp ON literals (s, p);
CREATE TABLE IF NOT EXISTS class_closure (
    descendant TEXT NOT NULL, ancestor TEXT NOT NULL,
    PRIMARY KEY (descendant, ancestor)
) WITHOUT ROWID;
"""


class GraphStore:
    """A SQLite-backed triple store for the evidence graph."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> GraphStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- writes (idempotent; INSERT OR IGNORE keeps triples a set) ---

    def add_edge(self, s: str, p: str, o: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO edges (s, p, o) VALUES (?, ?, ?)", (s, p, o)
        )

    def add_type(self, s: str, cls: str) -> None:
        self.add_edge(s, RDF_TYPE, cls)

    def add_literal(
        self, s: str, p: str, val: str, datatype: str = "xsd:string"
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO literals (s, p, val, datatype) VALUES (?, ?, ?, ?)",
            (s, p, val, datatype),
        )

    def add_subclass_closure(self, pairs: Iterable[tuple[str, str]]) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO class_closure (descendant, ancestor) VALUES (?, ?)",
            list(pairs),
        )

    def commit(self) -> None:
        self.conn.commit()

    # --- reads (always ordered) ---

    def objects(self, s: str, p: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT o FROM edges WHERE s = ? AND p = ? ORDER BY o", (s, p)
        ).fetchall()
        return [row[0] for row in rows]

    def subjects(self, p: str, o: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT s FROM edges WHERE p = ? AND o = ? ORDER BY s", (p, o)
        ).fetchall()
        return [row[0] for row in rows]

    def literal_values(self, s: str, p: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT val FROM literals WHERE s = ? AND p = ? ORDER BY val", (s, p)
        ).fetchall()
        return [row[0] for row in rows]

    def instances_of(self, cls: str) -> list[str]:
        """Every node whose type is ``cls`` or a subclass of it (via the materialised closure)."""
        rows = self.conn.execute(
            """
            SELECT DISTINCT e.s FROM edges e
            JOIN class_closure c ON e.o = c.descendant
            WHERE e.p = ? AND c.ancestor = ?
            ORDER BY e.s
            """,
            (RDF_TYPE, cls),
        ).fetchall()
        return [row[0] for row in rows]

    def is_a(self, node: str, cls: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM edges e
            JOIN class_closure c ON e.o = c.descendant
            WHERE e.s = ? AND e.p = ? AND c.ancestor = ?
            LIMIT 1
            """,
            (node, RDF_TYPE, cls),
        ).fetchone()
        return row is not None

    def edge_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])

    def literal_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM literals").fetchone()[0])

    def triple_count(self) -> int:
        """Total triples (object edges plus datatype literals)."""
        return self.edge_count() + self.literal_count()
