from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base for all modules.

    Import this in every module's models.py instead of defining a local Base.
    Alembic's env.py imports all module models so their metadata is registered
    against this single Base before autogenerate runs.
    """
    pass
