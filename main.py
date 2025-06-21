from fastapi import FastAPI
from pydantic import BaseModel
import base64
import tempfile
import logging
import re
from passporteye import read_mrz
from datetime import datetime

app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ImageBase64Request(BaseModel):
    image_base64: str

def clean_base64(b64_string: str):
    match = re.match(r"data:image/(?P<ext>\w+);base64,(?P<data>.+)", b64_string)
    if match:
        ext = match.group("ext")
        data = match.group("data")
        return data, ext
    return b64_string, "png"

def format_date(raw_date: str):
    try:
        return datetime.strptime(raw_date, "%y%m%d").strftime("%Y-%m-%d")
    except Exception:
        return None

def parse_kenyan_names(raw_name: str):
    # Split MRZ name string, ignore empty strings and "L"
    name_parts = [p for p in raw_name.split('<') if p and p != 'L']

    if len(name_parts) >= 3:
        # 3rd name is surname
        surname = name_parts[2]
        given_names = " ".join([p for i, p in enumerate(name_parts) if i != 2])
    elif len(name_parts) >= 1:
        # fallback: last is surname, others are given names
        surname = name_parts[-1]
        given_names = " ".join(name_parts[:-1])
    else:
        surname = ""
        given_names = ""

    return given_names.strip(), surname.strip()

@app.post("/mrz/")
async def extract_mrz(request: ImageBase64Request):
    try:
        # Step 1: Decode base64 and save image
        b64_data, ext = clean_base64(request.image_base64)
        image_data = base64.b64decode(b64_data)

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
            tmp.write(image_data)
            tmp_path = tmp.name
        logger.info(f"Image saved to temporary file: {tmp_path}")

        # Step 2: Extract MRZ from image
        mrz = read_mrz(tmp_path)
        if mrz is None:
            return {"status": "FAILURE", "error": "No MRZ found"}

        data = mrz.to_dict()

        # Step 3: Parse names (Kenyan ID logic)
        given_name, surname = parse_kenyan_names(data.get("surname", ""))

        # Step 4: Return structured response
        return {
            "mrz_type": data.get("mrz_type"),
            "document_code": data.get("type"),
            "issuer_code": data.get("country"),
            "surname": surname,
            "given_name": given_name,
            "document_number": data.get("number"),
            "document_number_checkdigit": data.get("check_number"),
            "nationality_code": data.get("nationality"),
            "birth_date": format_date(data.get("date_of_birth")),
            "sex": data.get("sex"),
            "expiry_date": format_date(data.get("expiration_date")),
            "optional_data": (data.get("optional1") or "") + (data.get("optional2") or ""),
            "mrz_text": data.get("raw_text"),
            "status": "SUCCESS"
        }

    except Exception as e:
        logger.error(f"Error during MRZ extraction: {e}")
        return {"status": "FAILURE", "error": str(e)}
