import io
import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, EmailStr
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

from database import create_document, get_documents, db

app = FastAPI(title="Panjarapol Go-Rakshan Sanstha API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DonationCreate(BaseModel):
    amount: int = Field(..., ge=1)
    donation_type: str = Field(..., description="Gud Daan | Adopt a Cow (monthly/year/lifetime) | Feed a Cow | General Fund")
    name: str
    email: EmailStr
    phone: str
    pan: Optional[str] = None
    show_amount_on_badge: bool = False


class DonationConfirm(BaseModel):
    order_id: str
    payment_method: str = Field(..., description="UPI | card | wallet | netbanking")
    payment_reference: Optional[str] = None


class VisitRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str
    preferred_date: str
    message: Optional[str] = None


class CSRInquiry(BaseModel):
    company_name: str
    contact_person: str
    email: EmailStr
    phone: str
    message: Optional[str] = None


@app.get("/")
def root():
    return {"service": "Panjarapol API", "status": "ok"}


@app.get("/test")
def test_database():
    status = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "collections": [],
    }
    try:
        if db is None:
            status["database"] = "❌ Not Configured"
        else:
            status["database"] = "✅ Connected"
            status["collections"] = db.list_collection_names()
    except Exception as e:
        status["database"] = f"⚠️ {str(e)[:120]}"
    return status


@app.post("/api/donations/create-order")
def create_order(payload: DonationCreate):
    # Create a mock payment order and store donation with status pending
    donation_doc = payload.model_dump()
    donation_doc.update({
        "status": "pending",
        "order_id": f"ORD-{int(datetime.utcnow().timestamp())}",
        "created_at": datetime.utcnow().isoformat(),
    })
    inserted_id = create_document("donation", donation_doc)
    return {
        "order_id": donation_doc["order_id"],
        "donation_id": inserted_id,
        "amount": payload.amount,
        "currency": "INR",
        "status": "pending",
        "message": "Mock order created. Proceed to confirm to simulate payment.",
    }


def _generate_receipt_pdf(donation: dict) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Branding colors
    c.setFillColorRGB(0.93, 0.52, 0.10)  # saffron
    c.rect(0, height - 40, width, 40, fill=1, stroke=0)

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20*mm, height - 25, "Shree Panjarapol Go-Rakshan Sanstha – Panvel (Est. 1908)")

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20*mm, height - 60, "80G Donation Receipt")

    y = height - 90
    c.setFont("Helvetica", 11)
    lines = [
        f"Receipt No: RCP-{donation.get('order_id', '')}",
        f"Date: {datetime.utcnow().strftime('%d-%m-%Y %H:%M IST')}",
        f"Donor: {donation.get('name')}",
        f"Email: {donation.get('email')} | Phone: {donation.get('phone')}",
        f"PAN: {donation.get('pan') or 'N/A'}",
        f"Donation Type: {donation.get('donation_type')}",
        f"Amount: INR {donation.get('amount')}",
        "This receipt is issued under section 80G of the Income Tax Act.",
        "Thank you for supporting Gau Seva.",
    ]
    for line in lines:
        c.drawString(20*mm, y, line)
        y -= 16

    c.setFont("Helvetica-Oblique", 10)
    c.setFillColorRGB(0.2, 0.5, 0.2)
    c.drawString(20*mm, 20*mm, "This is a system generated receipt and does not require a signature.")

    c.showPage()
    c.save()
    pdf = buffer.getvalue()
    buffer.close()
    return pdf


@app.post("/api/donations/confirm")
def confirm_order(payload: DonationConfirm):
    # Lookup donation by order_id
    docs = get_documents("donation", {"order_id": payload.order_id}, limit=1)
    if not docs:
        raise HTTPException(status_code=404, detail="Order not found")
    donation = docs[0]
    donation["status"] = "paid"
    donation["payment_method"] = payload.payment_method
    donation["payment_reference"] = payload.payment_reference or f"REF-{int(datetime.utcnow().timestamp())}"

    # Persist update
    db["donation"].update_one({"_id": donation["_id"]}, {"$set": {
        "status": donation["status"],
        "payment_method": donation["payment_method"],
        "payment_reference": donation["payment_reference"],
        "paid_at": datetime.utcnow(),
    }})

    # Generate receipt pdf in-memory and store to a simple bucket substitute (DB GridFS could be used). For demo, return bytes on demand.
    receipt_pdf = _generate_receipt_pdf(donation)

    # Cache in a transient collection so GET can fetch quickly
    db["receipt"].update_one({"order_id": payload.order_id}, {"$set": {
        "order_id": payload.order_id,
        "pdf": receipt_pdf,
        "created_at": datetime.utcnow()
    }}, upsert=True)

    return {
        "status": "paid",
        "order_id": payload.order_id,
        "donation_id": str(donation["_id"]),
        "receipt_url": f"/api/donations/{payload.order_id}/receipt.pdf",
        "badge_token": payload.order_id,  # reuse order id
        "message": "Payment confirmed. Receipt ready and badge can be generated.",
    }


@app.get("/api/donations/{order_id}/receipt.pdf")
def get_receipt(order_id: str):
    rec = db["receipt"].find_one({"order_id": order_id})
    if not rec:
        # Try to regenerate if donation exists
        docs = get_documents("donation", {"order_id": order_id}, limit=1)
        if not docs:
            raise HTTPException(status_code=404, detail="Receipt not found")
        pdf = _generate_receipt_pdf(docs[0])
    else:
        pdf = rec.get("pdf")
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename=receipt-{order_id}.pdf"
    })


def _draw_round_avatar(img: Image.Image, size: int) -> Image.Image:
    img = img.convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


@app.post("/api/badge/generate")
async def generate_badge(
    name: str = Form(...),
    show_amount: bool = Form(False),
    amount: Optional[str] = Form(None),
    slogan: Optional[str] = Form("I donated for a cause — when will you?"),
    photo: Optional[UploadFile] = File(None),
):
    # Create canvas
    width, height = 1080, 1350  # Instagram story friendly
    bg = Image.new("RGB", (width, height), (255, 248, 234))  # warm cream
    draw = ImageDraw.Draw(bg)

    # Saffron header
    draw.rectangle([0, 0, width, 220], fill=(235, 126, 18))

    # Green footer
    draw.rectangle([0, height - 180, width, height], fill=(34, 139, 34))

    # Title
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 64)
        body_font = ImageFont.truetype("DejaVuSans.ttf", 40)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 32)
    except Exception:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    title = "Shree Panjarapol Go-Rakshan Sanstha – Panvel"
    tw, th = draw.textlength(title, font=title_font), 64
    draw.text(((width - tw) / 2, 70), title, fill=(255, 255, 255), font=title_font)

    # Center donor photo
    avatar_size = 380
    y_center = 420
    if photo is not None:
        content = await photo.read()
        try:
            img = Image.open(io.BytesIO(content))
            avatar = _draw_round_avatar(img, avatar_size)
        except Exception:
            avatar = _draw_round_avatar(Image.new("RGB", (avatar_size, avatar_size), (240, 240, 240)), avatar_size)
    else:
        avatar = _draw_round_avatar(Image.new("RGB", (avatar_size, avatar_size), (240, 240, 240)), avatar_size)
    bg.paste(avatar, ((width - avatar_size) // 2, y_center - avatar_size // 2), avatar)

    # Name
    name_text = name
    nw = draw.textlength(name_text, font=body_font)
    draw.text(((width - nw) / 2, y_center + avatar_size // 2 + 30), name_text, fill=(42, 42, 42), font=body_font)

    # Amount (optional)
    if show_amount and amount:
        amount_text = f"Donated: ₹{amount}"
        aw = draw.textlength(amount_text, font=small_font)
        draw.text(((width - aw) / 2, y_center + avatar_size // 2 + 100), amount_text, fill=(90, 90, 90), font=small_font)

    # Slogan
    sw = draw.textlength(slogan, font=small_font)
    draw.text(((width - sw) / 2, height - 130), slogan, fill=(255, 255, 255), font=small_font)

    # NGO logo placeholder as sacred cow glyph
    draw.ellipse((40, height - 160, 160, height - 40), fill=(255, 255, 255))
    draw.text((60, height - 120), "Gau\nSeva", fill=(34, 139, 34), font=small_font, align="center")

    # Return PNG
    out = io.BytesIO()
    bg.save(out, format="PNG")
    out.seek(0)
    headers = {"Content-Disposition": "inline; filename=donor-badge.png"}
    return StreamingResponse(out, media_type="image/png", headers=headers)


@app.get("/api/content/info")
def content_info():
    return {
        "heritage": "Established in 1908, Shree Panjarapol Go-Rakshan Sanstha – Panvel has served as a sanctuary for cows and animals in distress.",
        "impact": {
            "cows": 100,
            "rescues_per_month": 12,
            "monthly_feed_cost_in_inr": 350000,
        },
        "gallery": [
            "https://images.unsplash.com/photo-1517849845537-4d257902454a",
            "https://images.unsplash.com/photo-1500595046743-cd271d694d30",
            "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee"
        ],
        "location": {
            "address": "Panvel, Maharashtra",
            "maps": "https://www.google.com/maps?q=Panvel+Go+Shala",
            "timings": "9 AM – 6 PM (All days)",
        },
        "csr": {
            "pitch": "Partner with us to create sustainable impact through Gau Seva. CSR donations are eligible under 80G.",
        }
    }


@app.post("/api/visit")
def visit(payload: VisitRequest):
    doc_id = create_document("visit", payload)
    return {"status": "ok", "id": doc_id, "message": "Visit request received. We will confirm shortly."}


@app.post("/api/csr")
def csr(payload: CSRInquiry):
    doc_id = create_document("csr", payload)
    return {"status": "ok", "id": doc_id, "message": "Thank you. Our team will reach out for CSR partnership."}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
