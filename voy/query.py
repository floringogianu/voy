"""SQL queries are defined here until we figure them out."""

create_tables = [
    """--sql
    CREATE TABLE IF NOT EXISTS author (
        id TEXT PRIMARY KEY,
        last_name TEXT NOT NULL,
        other_names TEXT NOT NULL,
        name_suffix TEXT,
        followed INTEGER CHECK( followed in (0, 1) ) NOT NULL
    ) STRICT
    """,
    """--sql
    CREATE TABLE IF NOT EXISTS paper (
        id TEXT PRIMARY KEY,
        updated TEXT NOT NULL,
        created TEXT NOT NULL,
        meta TEXT NOT NULL,
        visible INTEGER CHECK( visible in (0, 1) ) DEFAULT 1 NOT NULL
    ) STRICT
    """,
    """--sql
    CREATE TABLE IF NOT EXISTS authorship (
        id INTEGER PRIMARY KEY,
        author_id TEXT NOT NULL,
        paper_id TEXT NOT NULL,
        FOREIGN KEY (author_id) REFERENCES author (id),
        FOREIGN KEY (paper_id) REFERENCES paper (id)
    ) STRICT
    """,
    """--sql
    CREATE INDEX IF NOT EXISTS name_idx ON author(last_name, other_names)
    """,
    """--sql
    CREATE INDEX IF NOT EXISTS followed_idx ON author(followed)
    """,
    """--sql
    CREATE INDEX IF NOT EXISTS author_idx ON authorship(author_id)
    """,
]


# author  -----------------------------------------------------------------------------

is_author = """--sql
    SELECT EXISTS(SELECT 1 FROM author WHERE author.id = :id)
"""

add_author = """--sql
    INSERT INTO author (id, last_name, other_names, name_suffix, followed)
    VALUES (:id, :last_name, :other_names, :name_suffix, :followed);
"""

get_author = """--sql
    SELECT * FROM author WHERE author.id = :id
"""

get_followed = """--sql
    SELECT * FROM author WHERE author.followed = 1
"""

search_author = """--sql
    SELECT *
      FROM author
     WHERE last_name = :last_name
       AND other_names = :other_names
"""

search_by_last_name = """--sql
    SELECT *
      FROM author
     WHERE last_name = :last_name
"""

count_author = """--sql
    SELECT COUNT(*) FROM author
"""

follow_author = """--sql
    UPDATE author SET followed = :followed WHERE id = :id
"""

is_followed = """--sql
    SELECT author.followed FROM author WHERE author.id = :id
"""

count_followed = """--sql
    SELECT COUNT(*) FROM author WHERE author.followed = 1
"""

# paper  ------------------------------------------------------------------------------

is_paper = """--sql
    SELECT EXISTS(SELECT 1 FROM paper WHERE paper.id = :id)
"""

add_paper = """--sql
    INSERT INTO paper (id, updated, created, meta)
    VALUES (:id, :updated, :created, :meta);
"""

update_paper = """--sql
    UPDATE paper
        SET created = :created,
            updated = :updated,
            meta = :meta,
            visible = :visible
        WHERE id = :id
"""

get_paper = """--sql
    SELECT *
      FROM paper
     WHERE paper.id = :id
"""

count_paper = """--sql
    SELECT COUNT(*) FROM paper
"""

last_paper_by = """--sql
    SELECT * FROM paper ORDER BY {col:s} DESC LIMIT 1
"""

# authorship --------------------------------------------------------------------------

add_authorship = """--sql
    INSERT INTO authorship (author_id, paper_id)
    VALUES (:author_id, :paper_id);
"""


get_papers_by_author_id = """--sql
SELECT paper.* FROM
(SELECT * FROM author WHERE author.id = :aid) AS a
INNER JOIN authorship
    ON a.id = authorship.author_id
INNER JOIN paper
    ON authorship.paper_id = paper.id
WHERE paper.updated BETWEEN :starting_date AND datetime("now")
"""


get_papers_by_author_name = """--sql
SELECT paper.* FROM
(SELECT * FROM author
    WHERE author.last_name = :last_name
        AND author.other_names = :other_names
) AS a
INNER JOIN authorship
    ON a.id = authorship.author_id
INNER JOIN paper
    ON authorship.paper_id = paper.id
WHERE paper.updated BETWEEN :starting_date AND datetime("now")
"""


get_coauthors = """--sql
SELECT author.* FROM (SELECT * FROM paper WHERE paper.id = :pid) AS p
INNER JOIN authorship
    ON authorship.paper_id = p.id
INNER JOIN author
    ON author.id = authorship.author_id;
"""
