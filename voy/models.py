from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime as dt

from xxhash import xxh3_64_hexdigest, xxh3_64_intdigest

from . import CATEGORIES

log = logging.getLogger("voy")

PREFIX_MATCH = "van|der|de|la|von|del|della|da|mac|ter|dem|di|vaziri"
CAT_TERMS = " OR ".join([f"cat:{c}" for c in CATEGORIES])


__all__ = (
    "Author",
    "Paper",
    "PaperList",
)

PaperList = list["Paper"]


@dataclass
class Author:
    last_name: str
    other_names: str
    name_suffix: str | None = ""  # Why not = None?
    id: str | None = None
    _followed: bool | None = None
    _papers: PaperList | None = None

    def __post_init__(self):
        assert self.last_name.strip(), f"Author should have a last name, got {self}"
        # assert self.other_names.strip(), f"Should have a first name, got {self}."
        _id = xxh3_64_hexdigest(self.__hstr())  # f"{self.__hash__():x}"
        if self.id is None:
            self.id = _id
        assert self.id == _id, f"id mismatch: self._id={self.id}, computed={_id}"

    @classmethod
    def from_string(cls, string: str) -> Author:
        last, other, suffix = cls.normalize_author_name(string)
        return cls(last, other, suffix)

    @property
    def followed(self):
        return self._followed

    @property
    def papers(self):
        return self._papers

    @staticmethod
    def normalize_author_name(name: str) -> tuple[str, str, str]:
        """Copyright 2017 Cornell University

        Permission is hereby granted, free of charge, to any person obtaining a copy of
        this software and associated documentation files (the "Software"), to deal in
        the Software without restriction, including without limitation the rights to
        use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
        of the Software, and to permit persons to whom the Software is furnished to do
        so, subject to the following conditions:

        The above copyright notice and this permission notice shall be included in all
        copies or substantial portions of the Software.

        THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
        IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
        FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
        AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
        LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
        OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
        SOFTWARE.

        Credits: github.com/mattbierbaum/arxiv-public-datasets/
        """
        patterns = [
            (
                "double-prefix",
                r"^(.*)\s+(" + PREFIX_MATCH + r")\s(" + PREFIX_MATCH + r")\s(\S+)$",
            ),
            ("name-prefix-name", r"^(.*)\s+(" + PREFIX_MATCH + r")\s(\S+)$"),
            ("name-name-prefix", r"^(.*)\s+(\S+)\s(I|II|III|IV|V|Sr|Jr|Sr\.|Jr\.)$"),
            ("name-name", r"^(.*)\s+(\S+)$"),
        ]

        pattern_matches = (
            (mtype, re.match(m, name, flags=re.IGNORECASE)) for (mtype, m) in patterns
        )

        (mtype, match) = next(
            ((mtype, m) for (mtype, m) in pattern_matches if m is not None),
            ("default", None),
        )
        if match is None:
            author_entry = (name, "", "")
        elif mtype == "double-prefix":
            s = "{} {} {}".format(match.group(2), match.group(3), match.group(4))
            author_entry = (s, match.group(1), "")
        elif mtype == "name-prefix-name":
            s = "{} {}".format(match.group(2), match.group(3))
            author_entry = (s, match.group(1), "")
        elif mtype == "name-name-prefix":
            author_entry = (match.group(2), match.group(1), match.group(3))
        elif mtype == "name-name":
            author_entry = (match.group(2), match.group(1), "")
        else:
            author_entry = (name, "", "")

        return author_entry

    @property
    def names(self) -> tuple:
        return self.last_name, self.other_names, self.name_suffix

    def dict(self):
        return {
            "last_name": self.last_name,
            "other_names": self.other_names,
            "name_suffix": self.name_suffix,
            "id": self.id,
            "followed": self._followed,
        }

    def __hstr(self):
        """Change this and the hash changes."""
        last, other, sfx = self.last_name, self.other_names, self.name_suffix
        return f"{other} {last} {sfx}" if sfx else f"{other} {last}"

    def __hash__(self):
        """Python's hash is randomized between runtimes.
        But we need a way to quickly index authors in the database.
        """
        return xxh3_64_intdigest(self.__hstr())

    def __eq__(self, other):
        if isinstance(other, Author):
            return self.id == other.id
        return NotImplemented

    def __gt__(self, other):
        if isinstance(other, Author):
            return self.last_name > other.last_name
        return NotImplemented

    def __lt__(self, other):
        if isinstance(other, Author):
            return self.last_name < other.last_name
        return NotImplemented

    def __str__(self):
        last, other, sfx = self.last_name, self.other_names, self.name_suffix
        return f"{other} {last} {sfx}" if sfx else f"{other} {last}"

    def __format__(self, format_spec):
        return format(str(self), format_spec)


@dataclass
class PaperMeta:
    title: str
    abstract: str
    authors: list[Author]
    version: str  # TODO: needs fixing?
    categories: list[str]


class Paper:
    DATE_FMT = "%Y-%m-%d %H:%M:%S"

    def __init__(self, id, created, updated, meta, visible) -> None:
        self.id: str = id
        self.created: str = created
        self.updated: str = updated
        self.meta: PaperMeta = meta
        self.visible: bool = visible
        self._post_init()

    def _post_init(self):
        # the id is not versioned (doesn't end in v1).
        assert self.id[-1] != "v", f"{self.id} is not a valid arxiv identifier."
        # date should work with sqlite
        try:
            dt.strptime(self.updated, Paper.DATE_FMT)
        except ValueError as err:
            msg = f"{self.updated} is not a valid sqlite datetime (%Y-%m-%d %H:%M:%S)"
            raise ValueError(f"{msg}, {err}")
        assert self.created <= self.updated, (
            f"Can't update before publishing:\n{repr(self)}."
        )

    def dict(self):
        d = {k: v for k, v in self.__dict__.items() if k[0] != "_"}
        return {**d, "meta": asdict(self.meta)}

    def __str__(self) -> str:
        return f"({self.id}/{self.updated}) {self.meta.title}"

    def __repr__(self) -> str:
        body = ", ".join([f"{k}={v}" for k, v in self.__dict__.items()])
        return f"Paper({body})"

    def __format__(self, format_spec):
        return format(str(self), format_spec)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other):
        assert isinstance(other, Paper), "Can only compare to Paper."
        return self.id == other.id
