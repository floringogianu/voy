from __future__ import annotations

import curses
import logging
import math
import os
import re
from collections import defaultdict
from datetime import datetime as dt
from itertools import chain

from . import VOY_LOGS, cf
from .models import Author, Paper

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
    papers: list[Paper], offset: int = 0
) -> tuple[tuple[str, str], ...]:
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


def _list_papers(papers: list[Paper], coauthors: bool, url: bool, pfx=" ", sep=" "):
    """Lists paper title, date, co-authors and url, in the formats:

    {pfx}MM dd{sep}Title...

    {pfx}MM dd{sep}Title...
                   co-authors

    {pfx}MM dd{sep}Title...
                   url

    {pfx}MM dd{sep}Title...
                   co-authors
                   url
    """
    offset = len(pfx) + len(sep)
    for (date, title), paper in zip(_date_title_rows(papers, offset), papers):
        print(f"{pfx}{cf.bold | date}{sep}{cf.bold | title if coauthors else title}")
        if coauthors:
            print(_coauthor_rows(paper, len(date) + offset))
        if url:
            print(cf.cyan | f"{'':>{len(date)+offset}}https://arxiv.org/abs/{paper.id}")


def latest_papers(data: list[Author], num_papers: int, coauthors: bool, url: bool):
    slice_ = slice(0, num_papers or None)
    papers = sorted(
        set(chain.from_iterable([author.papers for author in data])),
        key=lambda p: p.updated,
        reverse=True,
    )[slice_]

    _list_papers(papers, coauthors, url, pfx="")


def paper_list(data: list[Paper]):
    """View of the paper titles."""
    papers = sorted(data, key=lambda p: p.updated, reverse=True)
    _list_papers(papers, coauthors=False, url=False, pfx="")


def author_paper_list(author: Author, num_papers: int, coauthors: bool, url: bool):
    slice_ = slice(0, num_papers or None)
    papers = sorted(author.papers, key=lambda p: p.updated, reverse=True)[slice_]

    # author header
    print(cf.yellow | author, "({})".format(cf.green | f"{len(author.papers)} papers"))

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


def _list_on_cols(data: list[str], cols: int = 1, sep_size: int = 3) -> None:
    rows = math.ceil(len(data) / cols)

    lines: dict[int, list] = defaultdict(list)
    max_len: dict[int, int] = {}
    for count, item in enumerate(data):
        lines[count % rows].append(item)
        max_len[count // rows] = max(max_len.get(count // rows, 0), len(item))

    for _, line in sorted(lines.items()):
        for cidx, item in enumerate(line):
            print(item.ljust(max_len[cidx] + sep_size), end="")
        print()


def author_table(authors: list[Author]) -> None:
    if authors[0].papers is not None:
        data = [
            "{} ({})".format(author, len(author.papers)) for author in sorted(authors)
        ]
    else:
        data = [str(author) for author in sorted(authors)]
    author_lenghts = sorted([len(s) for s in data], reverse=True)
    cols, sep = 5, 3
    while (sum(author_lenghts[:cols]) + (sep * cols)) > WIDTH:
        cols -= 1

    if authors[0].papers is not None:
        data = [
            "{} ({})".format(author, cf.green | len(author.papers))
            for author in sorted(authors)
        ]

    _list_on_cols(data, cols=cols)


def wordlist(string):
    """Split the string into individual elements and returning them as a list."""
    return re.split(r"(\s+|\n+)", string)


def wordwrap(window: curses._CursesWindow, string: str) -> None:
    """Word wrapper for Python Curses module by Ikaros Ainasoja."""

    # Get cursor position
    cursor_y, cursor_x = window.getyx()

    # Get window dimensions
    win_height, win_width = window.getmaxyx()

    # If string length <= window width: print the string.
    if len(string) + cursor_x <= win_width:
        window.addstr(string)

    # Otherwise, split it into individual words and whitespaces,
    # put them in a list and try to print them one at a time.
    else:
        for item in wordlist(string):
            # Skip spaces in the beginning of a new line.
            if cursor_x == 0 and item == " ":
                continue

            # If list item lenght <= distance to window edge: print it.
            if len(item) + cursor_x <= win_width:
                # we are on the last row, with space for just one character left.
                # the item fits (len=1) but it pushes the cursor out of bounds.
                if len(item) + cursor_x == win_width and cursor_y == win_height - 1:
                    return
                window.addstr(item)

            # Otherwise, move to the next line and try to fit it there.
            else:
                # If this would move the cursor out of bounds: error.
                if cursor_y == win_height - 1:
                    return

                # Otherwise, print it.
                window.addstr(cursor_y + 1, 0, item)

            # Get cursor position before the next list item.
            cursor_y, cursor_x = window.getyx()


def init_swipe_view(stdscr):
    # hide cursor
    curses.curs_set(0)
    # start colors in curses
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)

    def f(paper, status) -> None:
        # initialization
        stdscr.clear()
        stdscr.refresh()

        # terminal size
        height, width = stdscr.getmaxyx()

        # declare strings
        title: str = paper.meta.title
        abstract: str = paper.meta.abstract.replace("\n", " ")
        url = f"https://arxiv.org/abs/{paper.id}"
        footer = f"{status} | \u2190 (junk), \u2192 (keep), \u2193 (back), 'q' (exit)."

        # padding
        pad = 0.05
        start_x = int(width * pad)
        start_y = int(height * pad)
        content_width = int(width * (1 - pad * 2))
        title_rows = 1 if len(title) < content_width else 2
        url_rows, ftr_rows = 1, 1
        abs_rows = height - start_y - (title_rows + url_rows + ftr_rows) - 1
        abs_start_y = start_y + title_rows + url_rows + 1

        # title
        title_win = curses.newwin(title_rows, content_width, start_y, start_x)
        title_win.clear()
        title_win.attrset(curses.A_BOLD if paper.visible else curses.A_NORMAL)
        wordwrap(title_win, title)

        # url
        url_win = curses.newwin(url_rows, content_width, start_y + title_rows, start_x)
        url_win.clear()
        url_win.attrset(curses.color_pair(1) if paper.visible else curses.A_DIM)
        url_win.addstr(url)

        # abstract
        abstract_win = curses.newwin(abs_rows, content_width, abs_start_y, start_x)
        abstract_win.clear()
        abstract_win.attrset(curses.A_NORMAL if paper.visible else curses.A_DIM)
        wordwrap(abstract_win, abstract)

        # status bar
        footer_win = curses.newwin(ftr_rows, content_width, height - 1, start_x)
        footer_win.clear()
        footer_win.addstr(footer)

        # refresh everything
        url_win.refresh()
        title_win.refresh()
        abstract_win.refresh()
        footer_win.refresh()

    return f


# logging views ------------------------------------------------------------------------


def info(msg):
    log.info(msg)
    print(msg)


def critical(msg, *args, **kwargs):
    """Critical messages are the only ones communicated to the user."""
    msg = str(msg) + f"Check logs for details\n{VOY_LOGS}."
    log.critical(msg, *args, **kwargs)
