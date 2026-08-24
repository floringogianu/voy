"""This file plays two roles:
- contains all the Controllers that orchestrate user inputs, models and views.
- defines the argparser.
- is the entry point of the program :)
"""

from __future__ import annotations

import argparse
import csv
import curses
import datetime as dtm
import logging
import random
import sys
import time
from collections.abc import Sequence
from dataclasses import MISSING
from datetime import datetime as dt
from itertools import chain
from pathlib import Path

from datargs import arg, argsclass, parse

from . import VOY_LOGS, VOY_PATH
from . import query as Q
from . import views as V
from .models import Author, Paper
from .progress_bar import Progress
from .repo import AuthorArxiv, AuthorDB, PaperDB
from .seed import from_json
from .storage import Storage

log = logging.getLogger("voy")


# TODO: exit codes!!!


def show(opt: Show) -> None:
    with Storage() as db:
        if opt.author:  # fetch for one author
            authors = AuthorDB(db).search(" ".join(opt.author))
            if not authors:
                V.info(f"Found no author {opt.author}")
                return
            for author in authors:
                AuthorDB(db).get_papers_(author, opt.since, True)
        else:  # fetch for all followees
            authors = AuthorDB(db).get_followees()
            for followee in authors:
                AuthorDB(db).get_papers_(followee, opt.since, True)

    # list papers
    sorted_authors = sorted(authors, key=lambda author: author.other_names)
    if opt.by_author or opt.author:
        for author in sorted_authors:
            V.author_paper_list(author, opt.num, opt.coauthors, opt.url)
    else:
        V.latest_papers(sorted_authors, opt.num, opt.coauthors, opt.url, opt.page)


def search_author_in_db(searched: Sequence[str]) -> None:
    with Storage() as db:
        authors = AuthorDB(db).search(" ".join(searched))
    V.author_list(authors)


def search_text_in_db(searched: Sequence[str], coauthors: bool, url: bool) -> None:
    with Storage() as db:
        papers = PaperDB(db).full_text_search(searched)
    V.paper_list(papers, coauthors, url)


def search_author_in_arxiv(searched: Sequence[str], num_papers: int) -> None:
    res = AuthorArxiv.search(" ".join(searched), num_papers)
    authors = sorted(res, key=lambda a: (a.other_names, a.last_name))
    for author in authors:
        V.author_paper_list(author, num_papers, False, False)


def _one_year_back() -> dt:
    now = dt.now()
    try:
        since = now.replace(year=now.year - 1)
    except ValueError:
        since = now.replace(year=now.year - 1, day=now.day - 1)
    return since


def triage(opt: Triage) -> None:
    num_papers = 0

    with Storage() as db:
        authors = AuthorDB(db).get_followees()
        for followee in authors:
            AuthorDB(db).get_papers_(followee, _one_year_back())

    # sort the papers
    sorted_authors = sorted(authors, key=lambda author: author.other_names)
    slice_ = slice(0, num_papers or None)
    papers: list[Paper] = sorted(
        set(chain.from_iterable([author.papers for author in sorted_authors])),
        key=lambda p: p.updated,
        reverse=True,
    )[slice_]

    def controller(stdscr: curses._CursesWindow, view: callable, papers: list) -> None:
        """Uses `curses` to listen for input, retrieves the relevant data and
        displays it using `view`.
        """
        with Storage() as db:
            cursor, key = 0, 0
            while key != ord("q"):
                paper = papers[cursor]
                view(paper, f"Triaged {cursor}/{len(papers)} papers")

                # wait for next input
                key = stdscr.getch()

                # controls
                match key:
                    case curses.KEY_DOWN:
                        cursor = max(0, cursor - 1)
                    case curses.KEY_LEFT:
                        cursor = min(len(papers) - 1, cursor + 1)
                    case curses.KEY_RIGHT:
                        cursor = min(len(papers) - 1, cursor + 1)

                # triage paper
                if key == curses.KEY_LEFT and paper.visible:
                    paper.visible = False
                    PaperDB(db).update(paper)
                    db.commit()
                elif key == curses.KEY_RIGHT and not paper.visible:
                    paper.visible = True
                    PaperDB(db).update(paper)
                    db.commit()

    def run(stdscr):
        """A thin callable for curses.wrapper()."""
        view = V.init_swipe_view(stdscr)
        controller(stdscr, view, papers)

    curses.wrapper(run)


def update(opt) -> None:
    if opt.author:
        with Storage() as db:
            author = AuthorDB(db).get(Author.from_string(" ".join(opt.author)).id)
            if author is None:
                V.info("Can't update papers for authors not in the DB.")
                sys.exit(1)
            if not author.followed:
                V.info("Can't update papers for authors not followed.")
                sys.exit(1)
            followees = [author]
            last_paper = PaperDB(db).last()
    else:
        with Storage() as db:
            followees = list(AuthorDB(db).get_followees())
            last_paper = PaperDB(db).last()
        # temporary fix, add randomness
        random.shuffle(followees)

    # convert to the format required by ArXiv:
    # https://info.arxiv.org/help/api/user-manual.html#51-details-of-query-construction
    last_update_date = last_paper.created.replace(hour=0, minute=0)
    last_update_date = last_update_date.strftime("%Y%m%d%H%M")
    current_date = dt.now(dtm.UTC).strftime("%Y%m%d%H%M")

    V.info(
        "Updating {:n} authors you follow from {} to {}".format(
            len(followees),
            last_paper.created.strftime("%a, %b %d"),
            dt.now(dtm.UTC).strftime("%a, %b %d"),
        )
    )

    timeout, bsz = 10, 10
    batches = [set(followees[i : i + bsz]) for i in range(0, len(followees), bsz)]
    new_cnt, old_cnt, upd_cnt, tout_cnt = 0, 0, 0, 0
    db = Storage()
    prog = Progress(cleanup=True)
    prog.start()
    pbar = prog.track(
        batches, prefix="", context_func=lambda xs: ", ".join([a.last_name for a in xs])
    )
    for batch in pbar:
        _last_names = ",".join([a.last_name for a in batch])

        try:
            if opt.author:
                AuthorArxiv.get_papers_(batch)
            else:
                AuthorArxiv.get_papers_(batch, [last_update_date, current_date])
        except RuntimeError as err:
            V.info(f"failed while querying {_last_names}: {err}.")
            log.debug("failed querying %s: %s", _last_names, err)
            log.debug("sleep for %s seconds", 2 * timeout)
            time.sleep(2 * timeout)
            # TODO: should retry the failed batch
            continue

        tstart = time.time()
        for author in batch:
            if author.papers is None:
                log.info("update: no papers found for %s", author)
                continue

            # print(f" {author:24s}  |  {len(author.papers):3d} papers.", end="\r")
            for paper in author.papers:
                if PaperDB(db).exists(paper):
                    old = PaperDB(db).get(paper.id)
                    if old.meta.version == paper.meta.version:
                        old_cnt += 1
                    elif old.meta.version < paper.meta.version:
                        PaperDB(db).update(paper)
                        upd_cnt += 1
                    else:
                        log.error("paper version can either be higher or the same.")
                else:
                    # add paper
                    PaperDB(db).save(paper)
                    # and authors
                    for author in paper.meta.authors:
                        if not AuthorDB(db).exists(author):
                            author._followed = False
                            AuthorDB(db).save(author)

                        # add the relationship.
                        # TODO: where should this one stay :)
                        db(
                            Q.add_authorship,
                            {"author_id": author.id, "paper_id": paper.id},
                        )
                    new_cnt += 1
                # write to database
                db.commit()
        to_sleep = max(timeout - (time.time() - tstart), 0)
        log.debug("voy.update: sleep for %s", to_sleep)
        time.sleep(to_sleep)
    prog.stop()
    db.close()

    # print("\x1b[2K", end="\r")  # clean line
    V.info(
        "{:n} old, {:,} new, {:,} updated, timed-out {:,}".format(
            old_cnt, new_cnt, upd_cnt, tout_cnt
        )
    )


def follow(opt) -> None:
    log.debug(opt)
    searched = " ".join(opt.author)
    author = AuthorArxiv.get(searched)
    if author is None:
        V.info(f"Not found on arXiv, try `voy search {searched}`.")
    else:
        with Storage() as db:
            # TODO: checking status like this is ugly, there must be a better way
            if AuthorDB(db).exists(author):
                if AuthorDB(db).is_followed_(author).followed:
                    V.info(f"{author} already followed.")
                    return
                else:
                    author._followed = True
                    AuthorDB(db).update(author)
            else:
                author._followed = True
                AuthorDB(db).save(author)
            db.commit()

            followed = sorted(AuthorDB(db).get_followees())
            V.author_table(followed)
            V.info(f"\n{author} followed.")
            print(f"Following {len(followed)} authors.")


def unfollow(opt) -> None:
    log.debug(opt)
    with Storage() as db:
        result: list = list(AuthorDB(db).search(" ".join(opt.author)))
        match result:
            case []:
                V.info(f"{opt.author} not in the database.")
            case [author]:
                # TODO: checking status like this is ugly, there must be a better way
                if not AuthorDB(db).is_followed_(author).followed:
                    V.info(f"{author} not followed.")
                    return
                author._followed = False
                AuthorDB(db).update(author)
                db.commit()
                followed = AuthorDB(db).get_followees()
                V.author_table(list(followed))
                V.info(f"\n{author} unfollowed.")
                V.info(f"Now following {len(followed)} authors.")
            case [*authors]:
                followed = AuthorDB(db).get_followees()
                hits_in_followed = sorted([a for a in authors if a in followed])
                V.info(f"Multiple matches for {opt.author}, out of which you follow:")
                V.author_list(hits_in_followed)
                V.info("Be more specific by using the full name.")
            case _:
                # TODO should we show the user non-critical errors?
                log.error(f"pattern matching failed for {opt.author} -> {result}.")


def info(opt: Info) -> None:
    # TODO: make a proper info view
    # TODO: fix when database has not authors or no papers

    cnts = {}
    with Storage() as db:
        cnts = {
            "papers": PaperDB(db).count(),
            "authors": AuthorDB(db).count(),
            "followed": AuthorDB(db).count_followees(),
        }
        followees = sorted(AuthorDB(db).get_followees())
        for followee in followees:
            now = dt.now()
            AuthorDB(db).get_papers_(followee, now.replace(year=now.year - 2))

    print("data: {}\nlogs: {}".format(VOY_PATH, VOY_LOGS))

    # followees list
    if not followees:
        V.info("\nStart following authors with `voy add`.")
        return

    print("\nFollowing: ")
    V.author_table(followees)

    # other details
    print("\nDB details: ")
    for k, v in cnts.items():
        print(f"{k:16}{v:,}")
    with Storage() as db:
        last = PaperDB(db).last()
    print(
        "\nLast paper:\n[{}] {}".format(
            last.updated.strftime("%a, %b %d %Y"),
            last.meta.title,
        )
    )


def export(opt) -> None:
    with Storage() as db:
        followed = AuthorDB(db).get_followees()
    authors = sorted(followed, key=lambda a: (a.other_names, a.last_name))
    with open(opt.path, mode="w") as f:
        writer = csv.writer(f, delimiter=",", quotechar='"')
        for author in authors:
            writer.writerow([author.id, *author.names])


# argument parser config starts here  --------------------------------------------------


def _set_author_arg(default=False):
    return arg(
        default=() if default else MISSING,
        positional=True,
        help="eg.: Hinton | Geoff Hinton | Geoffrey Hinton",
    )


_pp = {"formatter_class": argparse.ArgumentDefaultsHelpFormatter}


@argsclass(
    description="""Without arguments it displays the most recent 10 papers from
    the authors you follow. Some useful arguments:

    `voy show Alex Graves`    most recent papers by an author.
    `voy show -a`             list the authors you follow and their last 3 papers.
    `voy show -n 20`          change the default number of papers being listed.
                              Works with all other arguments.
    """,
    parser_params={"formatter_class": argparse.RawDescriptionHelpFormatter},
)
class Show:
    author: Sequence[str] = _set_author_arg(default=True)
    by_author: bool = arg(
        "-a",
        default=False,
        help="authors you follow and their papers (default: %(default)s)",
    )
    num: int = arg(
        "-n",
        default=0,
        help="no. of papers to list (default: 10); if using `--by_author` (default: 3)",
    )
    page: int = arg(
        "-p",
        default=0,
        help="""activates pagination. Eg.: `-p 3 -n 5` shows papers 11 to 15
        (default: %(default)s)""",
    )
    coauthors: bool = arg(
        "-c", default=False, help="show co-authors (default: %(default)s)"
    )
    url: bool = arg("-u", default=False, help="show arixv URL (default: %(default)s)")
    since: str = arg(
        "-t",
        default=(),
        help="earliest date to retrieve papers, eg. 01.01.2019 (default: one year ago)",
    )

    def __post_init__(self):
        self.num = self.num or (3 if self.by_author else 10)
        self.since = (
            dt.strptime(self.since, "%d.%m.%Y") if self.since else _one_year_back()
        )
        # TODO: think about how these raised errors should be presented to the user.
        # We should be consistent with how we handle bubbled errors and it is related
        # to exit codes (which as of know are not implemented)
        if self.page < 0:
            raise ValueError("`--page/-p` can't be negative!")

    def run(self) -> None:
        show(self)


@argsclass(
    description="""Opens a 'swipe' view that allows quickly going through the
    most recent papers and reviewing them based on abstract.

    Left arrow key flags the papers so that it does not show up again until next
    update and increments the list. Right arrow key increments the list.
    """
)
class Triage:
    def run(self) -> None:
        triage(self)


@argsclass(
    description="""Fetch papers for followed authors; by default uses the arxiv
    API.

        1) arxiv API author match (default): A query with the name of the author
        is made and a fuzzy match of the past 100 papers or so is returned.
        2) arxiv json bulk (wip): uses the kaggle json dump.
        3) arxiv API bulk (todo): fetch batches of K latest papers going back
        and update only the the authors you follow.
    """,
    parser_params=_pp,
)
class Update:
    from_arxiv_json: Path | None = arg(
        help="path to the json downloaded from "
        + "`www.kaggle.com/datasets/Cornell-University/arxiv`",
    )
    author: Sequence[str] = arg(
        default=(),
        positional=True,
        help="""Name as returned by `voy info`, must be stored in the database.
        eg.: Geoff Hinton | Geoffrey Hinton.""",
    )
    # start_index: int = arg(default=1, help="starting arXiv API index")
    # stop_index: int = arg(default=10_001, help="maximum arXiv API index")

    def run(self) -> None:
        if self.from_arxiv_json:
            from_json(self)
        elif self.author:
            # V.info("Updating a single author is not yet implemented.")
            update(self)
        else:
            update(self)


@argsclass(
    description="""Search authors or papers on arxiv or in the local database.
    Some often used patterns could be:
    `voy search Sutton`
        search in the arxiv API for an author's last name, returns all the
        authors matching and their recent couple of papers. Useful for knowing
        exactly which authors to follow.
    `voy search --paper "average-reward" --db`
        full text search the paper titles and abstracts in the local db that
        match the term. Really useful for cutting through the noise when
        following many authors. Quotes can be omitted for single terms.
    """,
    parser_params={"formatter_class": argparse.RawDescriptionHelpFormatter},
)
class Search:
    author: Sequence[str] = _set_author_arg(True)
    paper: str | None = arg(
        default=(),
        help="""eg.: arxiv_id | average-reward | attention is all you need.
        Search through titles and abstracts for the input terms.""",
    )
    db: bool = arg(
        "-db",
        default=False,
        help="""Search in the local database (default: %(default)s)""",
    )
    coauthors: bool = arg(
        "-c", default=False, help="show co-authors (default: %(default)s)"
    )
    url: bool = arg("-u", default=False, help="show arixv URL (default: %(default)s)")
    num: int = arg(
        "-n",
        default=2,
        help="""number of papers to show for each author when searching on arXiv
        (default: %(default)s)""",
    )

    def run(self):
        assert self.author or self.paper, (
            "Either search for authors or you search for papers."
        )

        if self.author and not self.db:
            search_author_in_arxiv(self.author, self.num)
        elif self.author and self.db:
            search_author_in_db(self.author)
        elif self.paper and not self.db:
            V.info("Paper search on arxiv is not yet implemented.")
        elif self.paper and self.db:
            search_text_in_db(self.paper, self.coauthors, self.url)
        else:
            log.debug("Some value error in voy.search: %s", self)
            V.info("Something fishy happened, check logs.")


@argsclass(description="follow an author", parser_params=_pp)
class Follow:
    author: Sequence[str] = _set_author_arg()

    def run(self):
        follow(self)


@argsclass(description="follow an author", parser_params=_pp)
class Unfollow:
    author: Sequence[str] = _set_author_arg()

    def run(self):
        unfollow(self)


@argsclass
class Info:
    pass

    def run(self):
        info(self)


@argsclass
class Export:
    path: Path = arg(default=Path.cwd() / "voy.csv", positional=True, help="some path")

    def run(self):
        export(self)


@argsclass
class Voy:
    action: Search | Show | Triage | Follow | Unfollow | Update | Info | Export

    def run(self):
        self.action.run()


def main() -> None:
    args = parse(
        Voy,
        parser=argparse.ArgumentParser(
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="Voy: a CLI for following arXiv authors.",
            epilog="""The flow would go like this:
    `voy search Remi Munos` to search for an author. You should see a list of
        authors and their papers, because authors often use slight variations of
        their name and because the arxiv API is fairly fuzzy.

    `voy follow Rémi Munos`     to follow one of the results from above.
    `voy follow Remi Munos`     to follow yet another name variation of the same author.
    `voy update`                to fetch their papers and commit them to database.

    With the database updated you can now view the latest papers:

    `voy show`

    `voy show --help` check the options for displaying papers from authors you follow.
    `voy arg --help`  simillarly, check the options for the other arguments.

    IMPORTANT: sometimes the arxiv API returns no results, without error codes, even
    if the query is correct. Just let it cool for a while, it should be back eventualy.
            """,
        ),
    )
    log.info(args)

    args.run()


if __name__ == "__main__":
    main()
