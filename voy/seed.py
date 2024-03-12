import logging
import time
from datetime import datetime as dt

import jsonlines as jl

from . import CATEGORIES
from . import query as Q
from . import views as V
from .models import Author, AuthorDB, Paper, PaperDB, PaperMeta
from .storage import Storage

BATCH_SIZE = 100

log = logging.getLogger("voy")


def from_json(opt) -> None:
    """Uses Kaggle dataset."""

    def arxiv2sqlite_datetime(ts):
        to = dt.strptime(ts, "%a, %d %b %Y %H:%M:%S %Z")
        return dt.strftime(to, Paper.DATE_FMT)

    t0 = time.time()
    db = Storage()
    with jl.open(opt.from_arxiv_json, "r") as f:
        for jid, _paper in enumerate(f):
            paper_categories = _paper["categories"].split(" ")
            if any([x in CATEGORIES for x in paper_categories]):
                # make the paper
                paper = Paper(
                    id=_paper["id"],
                    created=arxiv2sqlite_datetime(_paper["versions"][0]["created"]),
                    updated=arxiv2sqlite_datetime(_paper["versions"][-1]["created"]),
                    meta=PaperMeta(
                        version=int(_paper["versions"][-1]["version"][-1]),
                        authors=[Author(*a[:3]) for a in _paper["authors_parsed"]],
                        title=_paper["title"].replace("\n", "").replace("  ", " "),
                        abstract=_paper["abstract"]
                        .replace("\n", "")
                        .replace("  ", " "),
                        categories=paper_categories,
                    ),
                )
                # add the paper
                PaperDB(db).save(paper)

                # add all the authors
                for author in paper.meta.authors:
                    if not AuthorDB(db).exists(author):
                        AuthorDB(db).save(author)

                    # add the relationship
                    db(Q.add_authorship, {"author_id": author.id, "paper_id": paper.id})

                # write to database
                db.commit()

            if jid % 100_000 == 0 and jid != 0:
                delta = time.time() - t0
                paper_num, author_num = PaperDB(db).count(), AuthorDB(db).count()
                V.info(
                    "[{:7d}] papers={:6d}, authors={:6d}  |  {:6.1f} i/s.".format(
                        jid, paper_num, author_num, paper_num / delta
                    )
                )
    V.info(f"Papers:  {PaperDB(db).count():n}")
    V.info(f"Authors: , {AuthorDB(db).count():n}")
    db.close()
