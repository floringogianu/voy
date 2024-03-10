import logging
import random
import sys
import time
from datetime import datetime as dt

import jsonlines as jl

from . import CATEGORIES
from . import query as Q
from . import views as V
from .lib.arxiv import get_response, parse_response
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


def fetch_batch(query, start_idx, max_tries=5):
    tries = 0
    while True:
        res = get_response(query, start_index=start_idx)
        papers = []
        for p in parse_response(res):
            paper = Paper(
                id=p["_id"],
                created=time.strftime(Paper.DATE_FMT, p["published_parsed"]),
                updated=time.strftime(Paper.DATE_FMT, p["updated_parsed"]),
                meta=PaperMeta(
                    title=p["title"].replace("\n", "").replace("  ", " "),
                    abstract=p["summary"].replace("\n", "").replace("  ", " "),
                    authors=[Author.from_string(a["name"]) for a in p["authors"]],
                    version=p["_version"],
                    categories=[t["term"] for t in p["tags"]],
                ),
            )
            papers.append(paper)

        # check if enough
        if len(papers) != BATCH_SIZE:
            log.warn("not a full batch, got %s instead; retrying", len(papers))
            time.sleep(2 + random.uniform(0, 4))
            tries += 1
            if tries >= max_tries:
                raise ValueError("arXiv API not reachable, exiting.")
            continue
        else:
            return sorted(papers, key=lambda p: p.updated)


def from_arxiv_api(opt) -> None:
    """Uses arXiv AIP."""
    db = Storage()
    query = "+OR+".join([f"cat:{cat}" for cat in CATEGORIES])

    start_idx = opt.start_index
    new_cnt, upd_cnt, old_cnt = 0, 0, 0
    while not ((old_cnt > 100) or (start_idx > opt.stop_index)):
        try:
            papers = fetch_batch(query, start_idx)
        except Exception as _:
            log.exception("Exception occured, exiting.")
            V.critical("arXiv API not reachable, exiting.")
            sys.exit()

        for paper in papers:
            if PaperDB(db).exists(paper):
                old = PaperDB(db).get(paper.id)
                if old.meta.version == paper.meta.version:
                    old_cnt += 1

                elif old.meta.version < paper.meta.version:
                    # update
                    # TODO: should check if authors are the same too :(
                    PaperDB(db).update(paper)
                    upd_cnt += 1
                else:
                    log.error("Paper version can either be higher or the same.")
            else:
                # add paper
                paper.save(db)
                # add the authors now
                for author in paper.meta.authors:
                    if not AuthorDB(db).exists(author):
                        AuthorDB(db).save(author)

                    # add the relationship
                    db(Q.add_authorship, {"author_id": author.id, "paper_id": paper.id})
                new_cnt += 1

            # write to database
            db.commit()

        V.info(
            "[@{:5d}] old: {:,}, new: {:,}, updated: {:,}".format(
                start_idx, old_cnt, new_cnt, upd_cnt
            )
        )

        start_idx += BATCH_SIZE
    db.close()
    V.info("new:  {:,}\nupd:  {:,}\nold:  {:,}".format(new_cnt, upd_cnt, old_cnt))
