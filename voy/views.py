import logging
import os
from datetime import datetime as dt
from itertools import chain
from typing import List, Tuple

from voy import VOY_LOGS, cf

from .models import AuthoredPapers, Paper

log = logging.getLogger("voy")


try:
    WIDTH = os.get_terminal_size().columns
except OSError:
    WIDTH = 80


def short_date(date):
    t = dt.strptime(date, Paper.DATE_FMT)
    return dt.strftime(t, "%b %d")


def _clip(seq: str, offset: int = 0) -> str:
    if len(seq) + offset > WIDTH:
        return f"{seq:.{WIDTH - (offset + 3)}s}..."
    return seq


def _date_title_rows(
    papers: List[Paper], offset: int = 0
) -> Tuple[Tuple[str, str], ...]:
    """Assumes an ordered set and returns rows of sparcely formatted (date, title)
    like so:

    Jan 20 title
        18 title
        12 title
    Dec 23 title
    ...
    """
    rows = []
    _month = ""
    for paper in papers:
        month, day = short_date(paper.updated).split(" ")
        if _month != month:
            _month = month
            m = month
        else:
            m = " " * len(month)
        date = f"{m} {day}"
        _title = _clip(paper.meta.title, len(date) + offset)
        rows.append((date, _title))
    return tuple(rows)


def _coauthor_rows(paper, indent_len):
    cos = []
    for author in paper.meta.authors:
        # TODO fix this type instability by moving out authors from meta?
        last, other, sfx = list(author.values())[:3]
        name = f"{other} {last} {sfx}" if sfx else f"{other} {last}"
        cos.append(name)
    w = os.get_terminal_size().columns - indent_len
    line = ", ".join(cos)
    if len(line) < w:
        return f"{' '*indent_len}{line}"
    co_str = cos.pop()
    # make the rows
    for co in cos:
        lines_so_far = co_str.split("\n")
        if len(lines_so_far[-1] + co) + 2 < w:
            co_str += ", " + co
        else:
            co_str += f",\n{' ' * indent_len}{cos.pop()}"
            # some papers just have sooooo many authors
            if len(lines_so_far) == 2 and len(cos) > 2:
                # assumes there's space for three authors per line
                # plus the extra markup
                co_str += f", ..., ..., {cos[-2]}, {cos[-1]}"
                break
    return f"{' '*indent_len}{co_str}"


def _list_papers(papers: List[Paper], coauthors: bool, url: bool, pfx=" ", sep=" "):
    """Lists paper title, date, co-authors and url, in the formats:

    {pfx}MM dd{sep}Title...

    {pfx}MM dd{sep}Title...
                   co-authors

    {pfx}MM dd{sep}Title...
                   link

    {pfx}MM dd{sep}Title...
                   co-authors
                   link
    """
    offset = len(pfx) + len(sep)
    for (date, title), paper in zip(_date_title_rows(papers, offset), papers):
        print(f"{pfx}{cf.bold | date}{sep}{cf.bold | title if coauthors else title}")
        if coauthors:
            print(_coauthor_rows(paper, len(date) + offset))
        if url:
            print(cf.cyan | f"{' ':>{len(date)+1}}https://arxiv.org/abs/{paper.id}")


def latest_papers(
    data: List[AuthoredPapers], num_papers: int, coauthors: bool, url: bool
):
    slice_ = slice(0, num_papers or None)
    papers = sorted(
        set(chain.from_iterable([apl.papers for apl in data if apl])),
        key=lambda p: p.updated,
        reverse=True,
    )[slice_]

    _list_papers(papers, coauthors, url, pfx="")


def author_paper_list(
    data: AuthoredPapers, num_papers: int, coauthors: bool, url: bool
):
    slice_ = slice(0, num_papers or None)
    papers = sorted(data.papers, key=lambda p: p.updated, reverse=True)[slice_]

    # author header
    author = data.author
    print(cf.yellow | author, "({})".format(cf.green | f"{len(data.papers)} papers"))

    _list_papers(papers, coauthors, url)


def author_list(authors):
    max_name_len = max([len(str(a)) for a in authors])
    for author in authors:
        print(
            "{0:{2}s}{1} |  {3}".format(
                str(author),
                cf.green | "followed" if author.followed else "",
                max_name_len + 3,
                cf.yellow | author.id,
            )
        )


def info(msg):
    log.info(msg)
    print(msg)


def critical(msg, *args, **kwargs):
    """Critical messages are the only ones communicated to the user."""
    msg = str(msg) + f"Check logs for details\n{VOY_LOGS}."
    log.critical(msg, *args, **kwargs)
