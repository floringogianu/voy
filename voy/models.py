from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime as dt
from typing import List, Optional, Tuple

from xxhash import xxh3_64_hexdigest, xxh3_64_intdigest

from . import query as Q
from .storage import Storage

PREFIX_MATCH = "van|der|de|la|von|del|della|da|mac|ter|dem|di|vaziri"


@dataclass
class Author:
    last_name: str
    other_names: str
    name_suffix: Optional[str] = ""
    followed: Optional[bool] = False
    id: Optional[int] = None
    # foreign
    _papers: Optional[List[Paper]] = None

    def __post_init__(self):
        _id = xxh3_64_hexdigest(self.__hstr())  # f"{self.__hash__():x}"
        if self.id is None:
            self.id = _id
        assert self.id == _id, f"id mismatch: self._id={self.id}, computed={_id}"
        # TODO not sure how to go about this stuff...
        with Storage() as db:
            if self.exists(db):
                self.followed = bool(db(Q.is_followed, {"id": self.id}).fetchone()[0])

    @classmethod
    def from_string(cls, string: str) -> Author:
        last, other, suffix = cls._normalize_author_name(string)
        return cls(last, other, suffix)

    @property
    def papers(self):
        return self._papers

    @classmethod
    def get(cls, id_: str, db: Storage) -> Author:
        res = db(Q.get_author, {"id": id_}).fetchone()
        _id, last, other, suffix, followed = res
        return cls(last, other, suffix, followed, _id)

    @classmethod
    def get_all_followed(cls, db: Storage) -> List[Author]:
        res = db(Q.get_followed, {}).fetchall()
        return [cls(last, other, sfx, flwd, id_) for id_, last, other, sfx, flwd in res]

    def exists(self, db):
        csr = db(Q.is_author, {"id": self.id})
        return bool(csr.fetchone()[0])

    def save(self, db):
        db(Q.add_author, self.dict())

    def update(self, db):
        """Only update that can happen is follow/unfollow."""
        db(Q.follow_author, {"id": self.id, "followed": int(self.followed)})

    def get_papers(self, db: Storage, starting_date: str) -> AuthoredPapers:
        """The reason we did all this.
        TODO: what's the place of this query?
        """
        csr = db(
            Q.get_papers_by_author_id, {"aid": self.id, "starting_date": starting_date}
        )
        res = csr.fetchall()
        if not res:
            return AuthoredPapers(self, [])

        self._papers = []
        for pid, updated, created, meta in res:
            meta = PaperMeta(**json.loads(meta))
            self._papers.append(Paper(pid, created, updated, meta))
        return AuthoredPapers(self, self._papers)

    @classmethod
    def count(cls, db, followed=False):
        if followed:
            return db(Q.count_followed, {}).fetchone()[0]
        return db(Q.count_author, {}).fetchone()[0]

    @staticmethod
    def _normalize_author_name(name: str) -> Tuple[str, str, Optional[str]]:
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

    def dict(self):
        return {k: v for k, v in self.__dict__.items() if k[0] != "_"}

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
            return str(self) == str(other)
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


class Paper:
    DATE_FMT = "%Y-%m-%d %H:%M:%S"

    def __init__(self, id, created, updated, meta) -> None:
        self.id: str = id
        self.created: str = created
        self.updated: str = updated
        self.meta: PaperMeta = meta
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
        assert (
            self.created <= self.updated
        ), f"Can't update before publishing:\n{repr(self)}."

    @classmethod
    def get(cls, id_, db):
        res = db(Q.get_paper, {"id": id_}).fetchone()
        return Paper._from_res(res)

    def exists(self, db):
        csr = db(Q.is_paper, {"id": self.id})
        return bool(csr.fetchone()[0])

    def save(self, db):
        d = self.dict()
        db(Q.add_paper, {**d, "meta": json.dumps(d["meta"])})

    def update(self, db):
        d = self.dict()
        db(Q.update_paper, {**d, "meta": json.dumps(d["meta"])})

    def dict(self):
        d = {k: v for k, v in self.__dict__.items() if k[0] != "_"}
        return {**d, "meta": asdict(self.meta)}

    def __str__(self) -> str:
        return f"({self.id}/{self.updated}) {self.meta.title}"

    def __repr__(self) -> str:
        body = ", ".join([f"{k}={v}" for k, v in self.__dict__.items()])
        return f"Paper({body})"

    @classmethod
    def count(cls, db):
        return db(Q.count_paper, {}).fetchone()[0]

    @classmethod
    def last(cls, db, col="created"):
        res = db(Q.last_paper_by.format(col=col), {}).fetchone()
        return Paper._from_res(res)

    @staticmethod
    def _from_res(res):
        id_, updated, created, meta = res
        meta = PaperMeta(**json.loads(meta))
        return Paper(id_, created, updated, meta)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, Paper):
            return self.id == other.id
        return NotImplemented


@dataclass
class PaperMeta:
    title: str
    abstract: str
    authors: List[Author]
    version: int
    categories: List[str]


@dataclass
class AuthoredPapers:
    author: Author
    papers: List[Paper]
