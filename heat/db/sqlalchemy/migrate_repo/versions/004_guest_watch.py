from sqlalchemy import *
from migrate import *


def upgrade(migrate_engine):
    meta = MetaData()
    meta.bind = migrate_engine

    guest_watch = Table(
        'guest_watch', meta,
        Column('id', Integer, primary_key=True),
        Column('created_at', DateTime(timezone=False)),
        Column('updated_at', DateTime(timezone=False)),
        Column('stack_name', String(length=255, convert_unicode=False,
                              assert_unicode=None,
                              unicode_error=None, _warn_on_bytestring=False)),
        Column('name', String(length=255, convert_unicode=False,
                              assert_unicode=None,
                              unicode_error=None, _warn_on_bytestring=False)),
        Column('state', String(length=255, convert_unicode=False,
                               assert_unicode=None,
                               unicode_error=None, _warn_on_bytestring=False)),
        Column('rule', Text()),
    )

    try:
        guest_watch.create()
    except Exception:
        meta.drop_all(tables=tables)
        raise

    guest_data = Table(
        'guest_data', meta,
        Column('id', Integer, primary_key=True),
        Column('created_at', DateTime(timezone=False)),
        Column('updated_at', DateTime(timezone=False)),
        Column('data', Text()),
    )

    try:
        guest_data.create()
    except Exception:
        meta.drop_all(tables=tables)
        raise


def downgrade(migrate_engine):
    meta = MetaData()
    meta.bind = migrate_engine

    guest_watch = Table('guest_watch', meta, autoload=True)
    guest_watch.drop()
    guest_data = Table('guest_data', meta, autoload=True)
    guest_data.drop()
