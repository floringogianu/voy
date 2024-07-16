"""This file plays two roles:
- contains all the Controllers that orchestrate user inputs, models and views.
- defines the argparser.
- is the entry point of the program :)
"""

from __future__ import annotations

import argparse
import csv
import platform
if platform.system() == "Windows":
    import windows_curses as curses
else:
    import curses
import logging
from dataclasses import MISSING
from datetime import datetime as dt
from itertools import chain
from pathlib import Path
from typing import Sequence

from arxiv import UnexpectedEmptyPageError
from datargs import arg, argsclass, parse

from . import VOY_LOGS, VOY_PATH
from . import query as Q
from . import views as V
from .models import Author, AuthorArxiv, AuthorDB, Paper, PaperDB
from .seed import from_json
from .storage import Storage

log = logging.getLogger("voy")


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
        V.latest_papers(sorted_authors, opt.num, opt.coauthors, opt.url)


def search_author_in_db(searched: Sequence[str]) -> None:
    with Storage() as db:
        authors = AuthorDB(db).search(" ".join(searched))
    V.author_list(authors)


def search_author_in_arxiv(searched: Sequence[str], max_results=100) -> None:
    res = AuthorArxiv.search(" ".join(searched), max_results)
    authors = sorted(res, key=lambda a: (a.other_names, a.last_name))
    for author in authors:
        V.author_paper_list(author, 2, False, False)
    if max_results:
        V.info(
            f"\nRestricted to a total of {max_results:n} entries. "
            + "Authors might be omitted. "
            + "Use option `--max 0` to show all author matches."
        )


def _one_year_back() -> str:
    now = dt.now()
    try:
        since = now.replace(year=now.year - 1)
    except ValueError:
        since = now.replace(year=now.year - 1, day=now.day - 1)
    return dt.strftime(since, Paper.DATE_FMT)


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
            author = AuthorDB(db).get(Author.from_string(opt.author).id)
            assert author.followed, "Can't update papers for authors you don't follow."
            followees = {author}
    else:
        with Storage() as db:
            followees = AuthorDB(db).get_followees()
        V.info(f"Updating {len(followees):n} authors you follow.")

    new_cnt, old_cnt, upd_cnt = 0, 0, 0
    db = Storage()
    for author in followees:
        try:
            AuthorArxiv.get_papers_(author)
        except UnexpectedEmptyPageError:
            V.info(f"error fetching papers by {author}.")
            continue

        if author.papers is None:
            log.info("update: no papers found for %s", author)
            continue

        print(f" {author:24s}  |  {len(author.papers):3d} papers.", end="\r")
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
    db.close()

    print("\x1b[2K", end="\r")  # clean line
    V.info("{:n} old, {:,} new, {:,} updated.".format(old_cnt, new_cnt, upd_cnt))


def follow(opt) -> None:
    log.debug(opt)
    result: list = list(AuthorArxiv.get(" ".join(opt.author)))
    with Storage() as db:
        match result:
            case []:
                V.info(f"{opt.author} not found on arXiv, try variations of the name.")
            case [author]:
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
            case [*authors]:
                V.author_list(authors)
                V.info(f"\nMultiple matches for {opt.author}.")
                V.info("Try again with one of the authors above.")
            case _:
                log.error(f"pattern matching failed for {opt.author} -> {result}.")


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
            AuthorDB(db).get_papers_(followee, "01-01-2020")

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
            last.updated.split(" ")[0],
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


@argsclass(description="list papers by authors you follow")
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
        self.since = self.since or _one_year_back()

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
    author: Sequence[str] = _set_author_arg(default=True)
    # start_index: int = arg(default=1, help="starting arXiv API index")
    # stop_index: int = arg(default=10_001, help="maximum arXiv API index")

    def run(self) -> None:
        if self.from_arxiv_json:
            from_json(self)
        elif self.author:
            V.info("Updating a single author is not yet implemented.")
        else:
            update(self)


@argsclass(description="search on arxiv by author", parser_params=_pp)
class Search:
    author: Sequence[str] = _set_author_arg(True)
    paper: str | None = arg(
        default=(), help="eg.: arxiv_id | Attention is all you need..."
    )

    def run(self):
        assert (
            self.author or self.paper
        ), "Either search for authors or you search for papers."

        if self.author:
            search_author_in_arxiv(self.author)
            # search_author_in_db(args.action.author)
        else:
            V.info("Paper search is not yet implemented.")


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

    `voy follow Rémi Munos`         to follow one of the results from above.
    `voy follow Remi Munos`         to follow yet another name variation of the same author.
    `voy update --from-arxiv-api`   to fetch their papers and commit them to database.

    With the database updated you can now view the latest papers:

    `voy show`

    `voy show --help` to check all the options for displaying the papers.

    IMPORTANT: often the arxiv API returns no results, without error codes, even
    if the query is correct. Just let it cool for a while, it should be back eventualy.
            """,
        ),
    )
    log.info(args)

    args.run()


if __name__ == "__main__":
    main()
