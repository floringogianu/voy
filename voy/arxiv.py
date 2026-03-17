import http.client
import logging
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime as dt
from enum import StrEnum
from http.client import RemoteDisconnected

from . import CATEGORIES
from .models import Author, Paper, PaperMeta

log = logging.getLogger("voy.arxiv")

__repo__ = "https://github.com/floringogianu/voy"
__user_agent__ = f"voy ({__repo__})"
__category_q__ = " OR ".join([f"cat:{c}" for c in CATEGORIES])
__base_url__ = "export.arxiv.org"


class SortCriterion(StrEnum):
    """Identifies a property by which search results can be sorted.

    See [the arXiv API User's Manual: sort order for return
    results](https://arxiv.org/help/api/user-manual#sort).
    """

    Relevance = "relevance"
    LastUpdatedDate = "lastUpdatedDate"
    SubmittedDate = "submittedDate"


class SortOrder(StrEnum):
    """Identifies a direction by which search results can be sorted.

    See [the arXiv API User's Manual: sort order for return
    results](https://arxiv.org/help/api/user-manual#sort).
    """

    Ascending = "ascending"
    Descending = "descending"


def parse_arxiv_xml(xml_str):
    """Parses Atom feed XML into a list of dictionaries."""
    # arXiv uses the Atom namespace
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(xml_str)

    papers = []
    for entry in root.findall("atom:entry", ns):
        id = entry.find("atom:id", ns).text.split("/abs/")[-1]
        pid, version = id.split("v")
        created = dt.fromisoformat(entry.find("atom:published", ns).text)
        updated = dt.fromisoformat(entry.find("atom:updated", ns).text)
        authors = [
            a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)
        ]
        paper = Paper(
            id=pid,
            created=created.strftime(Paper.DATE_FMT),
            updated=updated.strftime(Paper.DATE_FMT),
            meta=PaperMeta(
                title=entry.find("atom:title", ns).text.strip().replace("\n", " "),
                abstract=entry.find("atom:summary", ns)
                .text.strip()
                .replace("\n", " ")
                .replace("  ", " "),
                authors=[Author(*Author.normalize_author_name(a)) for a in authors],
                version=version,
                categories=[
                    cat.attrib.get("term") for cat in entry.findall("atom:category", ns)
                ],
            ),
            visible=True,
        )
        papers.append(paper)
    return papers


class ArXivClient:
    def __init__(
        self,
        max_results: int = 50,
        max_retries: int = 3,
        backoff_time: float = 3.1,
    ):
        """Makes requests to the ArXiv API and parses the response.
        Can manage the pagination.

        Args:
            max_results:  controls how many entries to render in one request,
                          starting from some index. It can be seen as a `page size`.
            max_retries:  how many times to make the same request, even if it
                          means reopening the connection.
            backoff_time: wait time after a 429 or a connection reset.
        """
        self._session = None
        self._max_results = max_results
        self._max_retries = max_retries
        self._backoff_time = backoff_time

    def _get_session(self):
        if self._session is None:
            self._session = http.client.HTTPSConnection(__base_url__)
        return self._session

    def _close_session(self):
        if self._session:
            self._session.close()
        self._session = None

    def _build_query(
        self,
        query: str,
        start: int,
        sort_by: SortCriterion,
        sort_order: SortOrder = SortOrder.Descending,
    ):
        full_query = f"({__category_q__}) AND ({query})"
        params = {
            "search_query": full_query,
            "start": start,
            "max_results": self._max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        return f"/api/query?{urllib.parse.urlencode(params)}"

    def get(
        self,
        query: str,
        start: int = 0,
        sort_by: SortCriterion = SortCriterion.Relevance,
    ):
        """Attempts several requests for a single query, waits between retries.

        Args:
            query: a well-formed ArXiv query to which we prepend categories and
                pagination-related tokens.
            start: the start index. Can be used for offsetting and returning more pages.

        Returns:
             List of Papers of size `self._max_results`.
        """
        query_ = self._build_query(query, start, sort_by)
        log.debug("GET %s, start=%s, sort_by=%s", query, start, sort_by)

        attempt = 0
        while attempt < self._max_retries:
            log.debug("attempt %s", attempt)
            session = self._get_session()
            try:
                session.request("GET", query_, headers={"User-Agent": __user_agent__})
                res = session.getresponse()

                if res.status == 200:
                    xml = res.read().decode("utf-8")
                    return parse_arxiv_xml(xml)

                elif res.status in (429, 500, 502, 503, 504):
                    res.read()
                    log.debug("transient HTTP error (%s): %s", res.status, res.reason)
                    raise Exception(f"transient HTTP error. {res.status}: {res.reason}")

                else:
                    res.read()
                    log.debug("terminal HTTP error (%s): %s", res.status, res.reason)
                    raise ValueError(f"terminal HTTP error. {res.status}: {res.reason}")

            except (
                RemoteDisconnected,
                BrokenPipeError,
                ConnectionError,
                Exception,
            ) as err:
                log.debug("connection terminated because %s", err)
                # connection was terminated by ArXiv or it failed
                attempt += 1

                # let it fail
                if isinstance(err, ValueError):
                    raise err

                self._close_session()

                if attempt >= self._max_retries:
                    raise RuntimeError(f"failed after {self._max_retries}") from err

                tout = self._backoff_time * 2**attempt
                time.sleep(tout)
                log.debug("cooled-off for %s, cleaned-up, reconnecting", tout)

    def get_many(
        self,
        query: str,
        total_results: int | None = None,
        sort_by: SortCriterion = SortCriterion.Relevance,
    ):
        """Gets all the entries returned by the API by calling `get` repeatedly,
        with an offset based on `max_results`.
        """

        # TODO: maybe a bit fishy? if the total_results < max_results, we cap
        # max_results to avoid unnecessary rendering.
        if total_results and total_results < self._max_results:
            self._max_results = total_results

        all_results = []

        start_idx = 0
        while True:
            res = self.get(query, start_idx, sort_by)

            if not res:
                break

            all_results.extend(res)

            # no more pages
            if len(res) < self._max_results:
                break

            # got enough
            if total_results and len(all_results) >= total_results:
                break

            time.sleep(self._backoff_time)
            start_idx += self._max_results

        self.close()
        log.info("query returned %s results", len(all_results))
        return all_results

    def close(self):
        self._close_session()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._close_session()


def main():
    with ArXivClient() as client:
        res = client.get_many(
            'au:"Razvan Pascanu" AND submittedDate:[202501010600 TO 202601010600]',
            sort_by=SortCriterion.SubmittedDate,
        )

    # if res:
    #     res = sorted(res, key=lambda p: dt.fromisoformat(p.created))
    for p in res:
        print(p)
    print(len(res))


if __name__ == "__main__":
    main()
