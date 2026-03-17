import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Sequence

from . import CATEGORIES
from . import query as Q
from .arxiv import ArXivClient, SortCriterion
from .models import Author, Paper, PaperList, PaperMeta
from .storage import Storage

log = logging.getLogger("voy.repo")

PREFIX_MATCH = "van|der|de|la|von|del|della|da|mac|ter|dem|di|vaziri"
CAT_TERMS = " OR ".join([f"cat:{c}" for c in CATEGORIES])


__all__ = (
    "AuthorDB",
    "AuthorArxiv",
    "PaperDB",
    "PaperArxiv",
)


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

    def get(self, id: str) -> Author | None:
        res = self.db(Q.get_author, {"id": id}).fetchone()
        if res is None:
            return None
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
        res = self.db(Q.fts, {"term": f'"{term}"'}).fetchall()
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

        '... AND (au:last other)':
            - it was used before 19.07.2025
            - much slower than without brackets
            - returns 5% less papers than without brackets on ~180 followees
        """
        sep = " AND " if with_and else " "
        sterm = sep.join([n for n in (last, other, suffix) if n])
        sterm = AuthorArxiv._sanitize(sterm)
        query = f"({CAT_TERMS}) AND au:{sterm}"
        log.debug("arxiv search term: %s", sterm)
        return query

    @staticmethod
    def search(
        searched: str,
        max_results=100,
        order_by=SortCriterion.Relevance,
    ) -> set[Author]:
        """Search the arXiv api for matching authors."""
        # query ArXiv
        with ArXivClient() as client:
            res = client.get_many(f'au:"{searched}"', max_results, order_by)
        # assign each Paper to the Author that matches last name
        last, _, _ = Author.normalize_author_name(searched)
        matches: dict[Author, PaperList] = defaultdict(list)
        for paper in res:
            for author in paper.meta.authors:
                if last in str(author):
                    matches[author].append(paper)
        # set the resulting Paper list to each Author match
        for author, papers in matches.items():
            author._papers = papers
        log.info(f"AuthorArxiv search: got {len(matches):n} matches.")
        return set(matches.keys())

    @staticmethod
    def get(
        searched: str,
        max_results=50,
        order_by=SortCriterion.Relevance,
    ) -> Author | None:
        """The ArXiv API does not have a way to return a single author.
        Therefore we search and return the Author if it is an exact match.
        """
        # a single ArXiv query of size 50
        with ArXivClient(max_results=max_results) as client:
            res = client.get(f'au:"{searched}"', sort_by=order_by)
        # nothing found
        if res is None:
            return None
        # assign each Paper to the Author that matches last name
        searched_author = Author.from_string(searched)
        for paper in res:
            for author in paper.meta.authors:
                if author == searched_author:
                    return author

    @staticmethod
    def get_papers_(
        authors: set[Author], date_range: list[str] | None = None
    ) -> set[Author]:
        # TODO: named get_papers_() but returns an Author :(
        # TODO: one does not simply modify a complex type
        _fnn = "AuthorArxiv.get_papers_"

        # do a batched query when many authors
        query = " OR ".join([f'au:"{author}"' for author in authors])
        if date_range is not None:
            query = "({}) AND submittedDate:[{}]".format(query, " TO ".join(date_range))

        res = []
        for sorting in (SortCriterion.SubmittedDate, SortCriterion.Relevance):
            with ArXivClient(max_results=100) as client:
                res += client.get_many(query, sort_by=sorting)
            if res:
                break
            log.debug(f"{_fnn}: no result by SubmittedDate, try Relevance")

        for author in authors:
            author._papers = []
        for paper in res:
            for author in authors:
                if author in paper.meta.authors:
                    author._papers.append(paper)
        for author in authors:
            log.info(f"{_fnn}: got %s papers for %s.", len(author._papers), str(author))
        return authors


class PaperArxiv(Rest[Paper]):
    """Implements arXiv Author rest layer."""

    def search(self, searched: str, max_results: int) -> set[Paper]:
        raise NotImplementedError


def main():
    batch = (
        # "Sebastian Flennerhag",
        # "Sina Ghiassian",
        # "Dileep George",
        # "Danijar Hafner",
        "Razvan Pascanu",
    )
    batch = {Author.from_string(a) for a in batch}
    res = AuthorArxiv.get_papers_(batch)
    for author in res:
        print(str(author), ":")
        for paper in author.papers:
            print(f"  {paper.meta.title}")


if __name__ == "__main__":
    main()
