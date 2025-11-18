from typing import Optional, Literal
from pydantic import BaseModel, Field, EmailStr

# Donation schema -> collection name: donation
class Donation(BaseModel):
    amount: int = Field(..., ge=1)
    donation_type: Literal[
        "Gud Daan",
        "Adopt a Cow (monthly)",
        "Adopt a Cow (year)",
        "Adopt a Cow (lifetime)",
        "Feed a Cow",
        "General Fund"
    ]
    name: str
    email: EmailStr
    phone: str
    pan: Optional[str] = None
    show_amount_on_badge: bool = False

# Visit requests -> collection name: visit
class Visit(BaseModel):
    name: str
    email: EmailStr
    phone: str
    preferred_date: str
    message: Optional[str] = None

# CSR inquiries -> collection name: csr
class CSR(BaseModel):
    company_name: str
    contact_person: str
    email: EmailStr
    phone: str
    message: Optional[str] = None
