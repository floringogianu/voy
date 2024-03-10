import argparse
import csv
import logging
from dataclasses import MISSING
from datetime import datetime as dt
from pathlib import Path
from typing import Optional, Sequence, Union

from datargs import arg, argsclass, parse

from . import views as V
from .models import AuthorArxiv, AuthorDB, Paper, PaperDB
from .storage import Storage
from .update import from_arxiv_api, from_json

log = logging.getLogger("voy")


def show(opt: "Show") -> None:
    with Storage() as db:
        if opt.author:  # fetch for one author
            authors = AuthorDB(db).search(" ".join(opt.author))
            if not authors:
                V.info(f"Found no author {opt.author}")
                return
            for author in authors:
                AuthorDB(db).get_papers_(author, opt.since)
        else:  # fetch for all followees
            authors = AuthorDB(db).get_followees()
            for followee in authors:
                AuthorDB(db).get_papers_(followee, opt.since)

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
    for apl in res:
        V.author_paper_list(apl, 3, False, False)
    if max_results > 0:
        V.info(
            f"\nRestricted to a total of {max_results:n} entries. "
            + "Authors might be omitted. "
            + "Use option `--max 0` to show all author matches."
        )


def follow(opt) -> None:
    log.debug(opt)
    with Storage() as db:
        result: list = list(AuthorDB(db).search(" ".join(opt.author)))
        match result:
            case []:
                V.info(f"{opt.author} not in the database, try variations of the name.")
            case [author]:
                # TODO: checking status like this is ugly, there must be a better way
                if AuthorDB(db).is_followed_(author).followed:
                    V.info(f"{author} already followed.")
                    return
                author._followed = True
                AuthorDB(db).update(author)
                db.commit()
                followed = AuthorDB(db).get_followees()
                V.author_list(followed)
                V.info(f"{author} followed.")
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
                V.author_list(followed)
                V.info(f"{author} unfollowed.")
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


def info(opt) -> None:
    # TODO make a proper info view
    # maybe show last index
    cnts = {}
    with Storage() as db:
        cnts = {
            "papers": PaperDB(db).count(),
            "authors": AuthorDB(db).count(),
            "followed": AuthorDB(db).count_followees(),
        }
        followed = sorted(AuthorDB(db).get_followees())

    if followed:
        print("Following: ")
        V.author_list(followed)

    print("DB details: ")
    for k, v in cnts.items():
        print(f"{k:16}{v:,}")
    with Storage() as db:
        last = PaperDB(db).last()
    print(
        "Last paper:\n[{}] {}".format(
            last.updated.split(" ")[0],
            last.meta.title,
        )
    )
    if not followed:
        V.info("\nStart following authors with `voy add`.")


def export(opt) -> None:
    with Storage() as db:
        followed = AuthorDB(db).get_followees()
    with open(opt.path, mode="w") as f:
        writer = csv.writer(f, delimiter=",", quotechar='"')
        for author in followed:
            writer.writerow([author.id, author.last_name, author.other_names])


# argument parser config starts here


def _set_author_arg(default=False):
    return arg(
        default=() if default else MISSING,
        positional=True,
        help="eg.: Marquez | Gabriel-Garcia Marquez.",
    )


_pp = {"formatter_class": argparse.ArgumentDefaultsHelpFormatter}


@argsclass(description="list papers by authors you follow")
class Show:
    author: Sequence[str] = _set_author_arg(default=True)
    by_author: bool = arg(
        "-f",
        default=False,
        help="authors you follow and their papers (default: %(default)s)",
    )
    num: int = arg(
        "-n",
        default=0,
        help="number of papers to list (default: 10); if `--by_author`, defaults to 3",
    )
    coauthors: bool = arg(
        "-c", default=False, help="show co-authors (default: %(default)s)"
    )
    url: bool = arg("-u", default=False, help="show arixv URL (default: %(default)s)")
    since: str = arg(
        "-t",
        default=(),
        help="earliest date to retrieve papers (default: one year ago)",
    )

    def __post_init__(self):
        self.num = self.num or (3 if self.by_author else 10)
        now = dt.now()
        try:
            since = now.replace(year=now.year - 1)
        except ValueError:
            since = now.replace(year=now.year - 1, day=now.day - 1)
        self.since = self.since or dt.strftime(since, Paper.DATE_FMT)


@argsclass(description="seed database", parser_params=_pp)
class Update:
    from_arxiv_json: Optional[Path] = arg(
        help="Path to the json downloaded from "
        + "`www.kaggle.com/datasets/Cornell-University/arxiv`",
    )
    from_arxiv_api: bool = arg(
        default=False, help="using the arXiv API to populate the database"
    )
    start_index: int = arg(default=1, help="starting arXiv API index")
    stop_index: int = arg(default=10_001, help="maximum arXiv API index")


@argsclass(description="search by author or by paper", parser_params=_pp)
class Search:
    author: Sequence[str] = _set_author_arg(True)
    paper: Optional[str] = arg(
        default=(), help="eg.: arxiv_id | Attention is all you need..."
    )


@argsclass(description="follow an author", parser_params=_pp)
class Add:
    author: Sequence[str] = _set_author_arg()
    delete: bool = arg("-d", default=False)


@argsclass
class Info:
    pass


@argsclass
class Export:
    path: Path = arg(default=Path.cwd() / "voy.csv", positional=True, help="some path")


@argsclass
class Voy:
    action: Union[Show, Add, Update, Search, Info, Export]


def main() -> None:
    args = parse(
        Voy,
        parser=argparse.ArgumentParser(
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
            description="Voy: a CLI for following arXiv authors.",
            epilog="Hint: start with `voy update --help` to fetch some data.",
        ),
    )
    log.info(args)

    # show
    if isinstance(args.action, Show):
        show(args.action)

    # update
    elif isinstance(args.action, Update):
        if args.action.from_arxiv_json:
            from_json(args.action)
        elif args.action.from_arxiv_api:
            from_arxiv_api(args.action)
        else:
            V.info(
                "Either update from arXiv API or from kaggle json. "
                + "See `voy update --help`.",
            )

    # search
    elif isinstance(args.action, Search):
        assert (
            args.action.author or args.action.paper
        ), "Either search for authors or you search for papers."
        if args.action.author:
            search_author_in_arxiv(args.action.author)
            # search_author_in_db(args.action.author)
        else:
            raise ValueError("No implementation for ", args)

    # follow
    elif isinstance(args.action, Add):
        if args.action.delete:
            unfollow(args.action)
        else:
            follow(args.action)

    # others
    elif isinstance(args.action, Info):
        info(args)
    elif isinstance(args.action, Export):
        export(args.action)
    else:
        raise ValueError("No implementation for ", args)


if __name__ == "__main__":
    main()
