from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime as dt

import arxiv
from xxhash import xxh3_64_hexdigest, xxh3_64_intdigest

from . import CATEGORIES
from . import query as Q
from .storage import Storage

log = logging.getLogger("voy")

PREFIX_MATCH = "van|der|de|la|von|del|della|da|mac|ter|dem|di|vaziri"
CAT_TERMS = " OR ".join([f"cat:{c}" for c in CATEGORIES])


__all__ = (
    "Author",
    "AuthorDB",
    "AuthorArxiv",
    "Paper",
    "PaperDB",
    "PaperArxiv",
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
        return self.normalize_author_name(str(self))

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

    def __format__(self, format_spec):
        return format(str(self), format_spec)


@dataclass
class PaperMeta:
    title: str
    abstract: str
    authors: list[Author]
    version: int
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
        assert (
            self.created <= self.updated
        ), f"Can't update before publishing:\n{repr(self)}."

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
        if isinstance(other, Paper):
            return self.id == other.id
        return NotImplemented


#  persistence layer


class Repository[T](ABC):
    @abstractmethod
    def get(self, id: str) -> T:
        raise NotImplementedError

    @abstractmethod
    def save(self, model: T) -> None:
        # TODO: should return something?
        raise NotImplementedError

    @abstractmethod
    def update(self, model: T) -> None:
        # TODO: should return something?
        raise NotImplementedError

    @abstractmethod
    def exists(self, model: T) -> bool:
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        raise NotImplementedError


class AuthorDB(Repository[Author]):
    """Implements SQLite Author persistence layer.

    It is meant to be used with a context manager:

    ```
    with Storage() as db:
        AuthorDB(db).get_papers(author)
    ```
    """

    def __init__(self, db: Storage) -> None:
        super().__init__()
        self.db = db

    def get(self, id: str) -> Author:
        res = self.db(Q.get_author, {"id": id}).fetchone()
        _id, last, other, suffix, followed = res
        return Author(last, other, suffix, _id, followed)

    # TODO: starting_date seems the wrong name
    def get_papers_(
        self,
        author: Author,
        starting_date: str,
        only_visible: bool = False,
    ) -> Author:
        """Retrieves the author's papers."""
        csr = self.db(
            Q.get_papers_by_author_id,
            {"aid": author.id, "starting_date": starting_date},
        )
        res = csr.fetchall()

        author._papers = [] if author._papers is None else author._papers
        for pid, updated, created, meta, visible in res:
            if only_visible and not visible:
                continue
            meta = PaperMeta(**json.loads(meta))
            author._papers.append(Paper(pid, created, updated, meta, bool(visible)))
        return author

    def get_followees(self) -> set[Author]:
        res = self.db(Q.get_followed, {}).fetchall()
        return set([Author(l, o, s, i, f) for i, l, o, s, f in res])

    def save(self, author: Author) -> None:
        self.db(Q.add_author, author.dict())

    def update(self, author: Author):
        """Only update that can happen is follow/unfollow."""
        self.db(Q.follow_author, {"id": author.id, "followed": int(author.followed)})

    def exists(self, author: Author) -> bool:
        csr = self.db(Q.is_author, {"id": author.id})
        return bool(csr.fetchone()[0])

    def is_followed_(self, author: Author) -> Author:
        if not self.exists(author):
            return author
        author._followed = bool(self.db(Q.is_followed, {"id": author.id}).fetchone()[0])
        return author

    def count(self) -> int:
        return self.db(Q.count_author, {}).fetchone()[0]

    def count_followees(self) -> int:
        return self.db(Q.count_followed, {}).fetchone()[0]

    def search(self, searched: str) -> set[Author]:
        last, other, suffix = Author.normalize_author_name(searched)
        log.debug("DB search %s, %s, %s", last, other, suffix)

        if other:
            csr = self.db(Q.search_author, {"last_name": last, "other_names": other})
            res = csr.fetchall()
        else:
            csr = self.db(Q.search_by_last_name, {"last_name": last})
            res = csr.fetchall()

        if not res:
            return set()
        # aid, last name, other names, suffix
        authors = set(sorted([Author(r[1], r[2], r[3], r[0], bool(r[4])) for r in res]))
        return authors


class PaperDB(Repository[Paper]):
    """Implements SQLite Paper persistence layer."""

    def __init__(self, db: Storage) -> None:
        super().__init__()
        self.db = db

    @staticmethod
    def _from_res(res):
        """Helper method that initializes a Paper."""
        id_, updated, created, meta, visible = res
        meta = PaperMeta(**json.loads(meta))
        return Paper(id_, created, updated, meta, bool(visible))

    def get(self, id: str) -> Paper:
        res = self.db(Q.get_paper, {"id": id}).fetchone()
        return self._from_res(res)

    def exists(self, paper: Paper) -> bool:
        csr = self.db(Q.is_paper, {"id": paper.id})
        return bool(csr.fetchone()[0])

    def save(self, paper: Paper) -> None:
        data = paper.dict()
        self.db(Q.add_paper, {**data, "meta": json.dumps(data["meta"])})

    def update(self, paper: Paper) -> None:
        data = paper.dict()
        self.db(Q.update_paper, {**data, "meta": json.dumps(data["meta"])})

    def count(self) -> int:
        return self.db(Q.count_paper, {}).fetchone()[0]

    def last(self, col: str = "created") -> Paper:
        res = self.db(Q.last_paper_by.format(col=col), {}).fetchone()
        return self._from_res(res)

    def full_text_search(self, term: Sequence[str]) -> list[Paper]:
        res = self.db(Q.fts, {"term": term}).fetchall()
        return [self._from_res(r) for r in res]


class Rest[T](ABC):
    """A REST interface."""

    @abstractmethod
    def search(self, searched: str, max_results: int) -> set[T]:
        raise NotImplementedError


class AuthorArxiv(Rest[Author]):
    """Implements arXiv Author rest layer."""

    @staticmethod
    def _sanitize(q: str) -> str:
        return q.replace("'", "\\")  # "d'Oro -> d\Oro"

    @staticmethod
    def _make_query(last: str, other: str, suffix: str, with_and=False) -> str:
        """Not sure which is better:

        'au: last other':
            - seems to be the least selective, for common last names it will
            return a lot of them.
            - fails to return Pablo S. Castro for Pablo Samuel Castro or any of
            the variations.

        'au: last AND other':
            - fails to return Rémi Munos for Rémi Munos
        """
        sep = " AND " if with_and else " "
        sterm = sep.join([n for n in (last, other, suffix) if n])
        sterm = AuthorArxiv._sanitize(sterm)
        query = f"({CAT_TERMS}) AND (au:{sterm})"
        log.debug("arxiv search term: %s", sterm)
        return query

    @staticmethod
    def get(
        searched: str, max_results=100, order_by=arxiv.SortCriterion.Relevance
    ) -> set[Author]:
        last, other, suffix = Author.normalize_author_name(searched)

        # cast a wider net by ignoring the first other name
        _other = other.split(" ")[0] if " " in other else other
        matches: set[Author] = AuthorArxiv.search(" ".join([_other, last]), max_results)

        # exact match with the original search term
        for author in matches:
            if (other == author.other_names) and (suffix == author.name_suffix):
                return {author}
        return matches

    @staticmethod
    def search(
        searched: str, max_results=100, order_by=arxiv.SortCriterion.Relevance
    ) -> set[Author]:
        """Search the arXiv api for matching authors."""
        last, other, suffix = Author.normalize_author_name(searched)

        res = arxiv.Client().results(
            arxiv.Search(
                AuthorArxiv._make_query(last, other, suffix),
                sort_by=order_by,
                max_results=max_results,
            )
        )
        # extract the set of authors that match
        matches: dict[Author, PaperList] = defaultdict(list)
        for _paper in res:
            for _author in _paper.authors:
                author = Author.from_string(_author.name)
                if last in str(author):
                    matches[author].append(PaperArxiv._cast(_paper))
        for author, papers in matches.items():
            author._papers = papers
        log.info(f"AuthorArxiv search: got {len(matches):n} matches.")
        return set(matches.keys())

    @staticmethod
    def get_papers_(author: Author) -> Author:
        # TODO: one does not simply modify a complex type
        res = AuthorArxiv.get(str(author), order_by=arxiv.SortCriterion.LastUpdatedDate)
        if not res:
            res = AuthorArxiv.get(str(author), order_by=arxiv.SortCriterion.Relevance)
        if res:
            _author = res.pop()
            author._papers = _author.papers
            log.info(
                f"AuthorArxiv.papers: got {len(author.papers):n} papers for {author:s}."
            )
        return author


class PaperArxiv(Rest[Paper]):
    """Implements arXiv Author rest layer."""

    def search(self, searched: str, max_results: int) -> set[Paper]:
        raise NotImplementedError

    @staticmethod
    def _cast(paper: arxiv.Result) -> Paper:
        pid, version = paper.entry_id.split("/")[-1].split("v")

        return Paper(
            id=pid,
            created=time.strftime(Paper.DATE_FMT, paper._raw.published_parsed),
            updated=time.strftime(Paper.DATE_FMT, paper._raw.updated_parsed),
            meta=PaperMeta(
                title=paper.title,
                abstract=paper.summary,
                authors=[
                    Author(*Author.normalize_author_name(a.name)) for a in paper.authors
                ],
                version=version,
                categories=paper.categories,
            ),
            visible=True,
        )
