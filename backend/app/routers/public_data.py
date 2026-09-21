"""Авторизованный просмотр публичных справочников и независимых измерений."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import OfficialSchool, PublishedConnection, User
from ..security import current_user
from ..services.external_network import network_context
from ..services.public_data import connection_dict

router = APIRouter(prefix='/api/public-data', tags=['Public data'])


@router.get('/schools')
def schools(search: str = '', offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
            db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = db.query(OfficialSchool)
    if search.strip():
        term = '%' + search.strip() + '%'
        query = query.filter(OfficialSchool.district.ilike(term) | OfficialSchool.settlement.ilike(term) | OfficialSchool.address.ilike(term))
    total = query.count()
    rows = query.order_by(OfficialSchool.external_id).offset(offset).limit(limit).all()
    return {'total': total, 'offset': offset, 'items': [
        {c.name: getattr(r, c.name) for c in r.__table__.columns if c.name != 'raw_json'} for r in rows],
        'note': 'Справочник eGov от 27.04.2024 помечен источником как неактуальный; названия школ не опубликованы. Требуется сверка с действующими организациями.'}


@router.get('/connections')
def connections(db: Session = Depends(get_db), user: User = Depends(current_user)):
    # Only public facts; no school contacts, devices or operational metrics.
    return [connection_dict(r) for r in db.query(PublishedConnection).order_by(PublishedConnection.school_name).all()]


@router.get('/network')
def network(at: datetime | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return network_context(db, at)
