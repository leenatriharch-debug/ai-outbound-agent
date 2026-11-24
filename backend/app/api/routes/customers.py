from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.customer import (
    CustomerCreate,
    CustomerUpdate,
    CustomerResponse
)
from app.controllers.customer_controller import CustomerController
from app.services.auth_service import require_roles

router = APIRouter(
    prefix="/api/customers",
    tags=["Customers"],
    dependencies=[Depends(require_roles("admin", "operator"))],
)


@router.post("/", response_model=CustomerResponse, status_code=201)
def create_customer(data: CustomerCreate, db: Session = Depends(get_db)):
    return CustomerController.create_customer(db, data)


@router.get("/", response_model=list[CustomerResponse])
def get_customers(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db)
):
    return CustomerController.get_customers(db, skip, limit)


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    return CustomerController.get_customer(db, customer_id)


@router.put("/{customer_id}", response_model=CustomerResponse)
def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    db: Session = Depends(get_db)
):
    return CustomerController.update_customer(db, customer_id, data)


@router.patch("/{customer_id}/deactivate")
def deactivate_customer(
    customer_id: int,
    db: Session = Depends(get_db)
):
    return CustomerController.deactivate_customer(db, customer_id)