# ruff: noqa
# app/repositories/vendor_repository.py
from sqlmodel import Session, select
from bench.import_repos.multivendor.app.models.vendor import Vendor


def get_by_id(session: Session, vendor_id: int) -> Vendor | None:
    return session.get(Vendor, vendor_id)


def get_by_user_id(session: Session, user_id: int) -> Vendor | None:
    statement = select(Vendor).where(Vendor.user_id == user_id)
    return session.exec(statement).first()


def create(session: Session, vendor: Vendor) -> Vendor:
    session.add(vendor)
    session.commit()
    session.refresh(vendor)
    return vendor


def update(session: Session, vendor: Vendor, data: dict) -> Vendor:
    for key, value in data.items():
        setattr(vendor, key, value)
    session.add(vendor)
    session.commit()
    session.refresh(vendor)
    return vendor