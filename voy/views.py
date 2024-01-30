import os
from datetime import datetime as dt
from itertools import chain
from typing import List, Tuple

from voy import cf

from .models import AuthoredPapers, Paper


def short_date(date):
    t = dt.strptime(date, Paper.DATE_FMT)
    return dt.strftime(t, "%b %d")


def _clip(seq: str, offset: int = 0) -> str:
    w = os.get_terminal_size().columns
    if len(seq) + offset > w:
        return f"{seq:.{w - (offset + 3)}s}..."
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


def latest_papers(data: List[AuthoredPapers], num_papers: int, coauthors: bool):
    slice_ = slice(0, num_papers or None)
    papers = sorted(
        set(chain.from_iterable([apl.papers for apl in data if apl])),
        key=lambda p: p.updated,
        reverse=True,
    )[slice_]

    for (date, title), paper in zip(_date_title_rows(papers, 1), papers):
        print(f"{cf.bold | date} {cf.bold | title if coauthors else title}")
        if coauthors:
            print(_coauthor_rows(paper, len(date) + 1))


def author_paper_list(data: AuthoredPapers, num_papers: int, coauthors: bool):
    slice_ = slice(0, num_papers or None)
    papers = sorted(data.papers, key=lambda p: p.updated, reverse=True)[slice_]

    # author header
    author = data.author
    print(cf.yellow | author, "({})".format(cf.green | f"{len(data.papers)} papers"))

    pre, sep = " ", " "
    offset = len(pre) + len(sep)
    for (date, title), paper in zip(_date_title_rows(papers, offset), papers):
        title_ = cf.bold | title if coauthors else title
        print(f"{pre}{cf.bold | date}{sep}{title_}")
        if coauthors:
            print(_coauthor_rows(paper, len(date) + offset))


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
