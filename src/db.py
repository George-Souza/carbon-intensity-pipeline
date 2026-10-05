import os

from sqlalchemy import Engine, create_engine


def get_engine() -> Engine:
    database_url = os.environ["DATABASE_URL"]
    return create_engine(database_url)
