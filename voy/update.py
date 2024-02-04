import logging
import random
import re
import sys
import time
from datetime import datetime as dt
from typing import Optional, Tuple

import jsonlines as jl

from . import CATEGORIES
from . import query as Q
from . import views as V
from .lib.arxiv import get_response, parse_response
from .models import Author, Paper, PaperMeta
from .storage import Storage

PREFIX_MATCH = "van|der|de|la|von|del|della|da|mac|ter|dem|di|vaziri"
BATCH_SIZE = 100

log = logging.getLogger("voy")


def from_json(opt):
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
                paper.save(db)

                # add all the authors
                for author in paper.meta.authors:
                    if not author.exists(db):
                        author.save(db)

                    # add the relationship
                    db(Q.add_authorship, {"author_id": author.id, "paper_id": paper.id})

                # write to database
                db.commit()

            if jid % 100_000 == 0 and jid != 0:
                delta = time.time() - t0
                paper_num, author_num = Paper.count(db), Author.count(db)
                V.info(
                    "[{:7d}] papers={:6d}, authors={:6d}  |  {:6.1f} i/s.".format(
                        jid, paper_num, author_num, paper_num / delta
                    )
                )
    V.info("Papers:  ", Paper.count(db))
    V.info("Authors: ", Author.count(db))
    db.close()


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
                    authors=[
                        Author(*_normalize_author_name(a["name"])) for a in p["authors"]
                    ],
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


def from_arxiv_api(opt):
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
            if paper.exists(db):
                old = Paper.get(paper.id, db)
                if old.meta.version == paper.meta.version:
                    old_cnt += 1

                elif old.meta.version < paper.meta.version:
                    # update
                    # TODO should check if authors are the same too :(
                    paper.update(db)
                    upd_cnt += 1
                else:
                    log.error("Paper version can either be higher or the same.")
            else:
                # add paper
                paper.save(db)
                # add the authors now
                for author in paper.meta.authors:
                    if not author.exists(db):
                        author.save(db)

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
