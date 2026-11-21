from fastapi import APIRouter,Depends
from src.middlewares.auth import current,writer
from src.schemas.business import QuarantineInput
from src.services import quarantines
from src.services.audit import log
router=APIRouter()
@router.get('/quarantines')
def listing(u=Depends(current)):return quarantines.list_items()
@router.get('/quarantines/{item_id}')
def detail(item_id:int,u=Depends(current)):return quarantines.detail(item_id)
@router.post('/quarantines')
def create(data:QuarantineInput,u=Depends(writer)):
 q=quarantines.create(data,u);log(u.email,'quarantine.register',q['code']);return q
@router.post('/quarantines/{item_id}/activate')
def activate(item_id:int,u=Depends(writer)):
 before=quarantines.detail(item_id)
 q=quarantines.activate(item_id,u)
 if before['status']!='active':log(u.email,'quarantine.activate',q['code'])
 return q
@router.post('/quarantines/{item_id}/revoke')
def revoke(item_id:int,u=Depends(writer)):
 before=quarantines.detail(item_id)
 q=quarantines.revoke(item_id,u)
 if before['status']!='revoked':log(u.email,'quarantine.revoke',q['code'])
 return q
