import sqlite3

import voy.query as Q
import voy.views as V
from voy import VOY_PATH


class Storage:
    DB_PATH = VOY_PATH / "voy.db"

    def __init__(self, in_memory=False) -> None:
        try:
            self.con = sqlite3.connect(Storage.DB_PATH)
        except:
            V.info("Trying to open connection: ", Storage.DB_PATH)
            raise

        if in_memory:
            self.con = sqlite3.connect(":memory:")
            sqlite3.connect(Storage.DB_PATH).backup(self.con)

        self.csr = self.con.cursor()

        self._config()
        self._setup()

    def _setup(self):
        """Create tables."""
        for create_statement in Q.create_tables:
            self.csr.execute(create_statement)
        self.con.commit()

    def _config(self):
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute("pragma journal_mode=wal")
        self.con.execute("pragma synchronous = normal")
        self.con.execute("pragma temp_store = memory")
        self.con.execute("pragma mmap_size = 30000000000")

    # TODO: is the return really Any?
    def __call__(self, q, params):
        return self.csr.execute(q, params)

    def many(self, q, params):
        return self.csr.executemany(q, params)

    def commit(self):
        self.con.commit()

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, ext_type, exc_value, traceback):
        self.csr.close()
        if isinstance(exc_value, Exception):
            self.con.rollback()
        else:
            self.con.commit()
        self.con.close()
