from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, EmailStr
import mysql.connector
import logging
import os
import base64
import tempfile
import re
from passporteye import read_mrz
from datetime import datetime
from typing import List, Optional, Tuple

app = FastAPI()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("mrz_parser.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# MySQL connection settings (use environment variables in production)
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "password")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "mrzdb")

class ImageBase64Request(BaseModel):
    image_base64: str

class RegistrationRequest(BaseModel):
    fullName: str
    email: EmailStr
    mobileNumber: str
    areaOfResidence: str
    emergencyContactName: str
    relationship: str
    emergencyContactMobileNumber: str

class RegistrationResponse(BaseModel):
    id: int
    fullName: str
    email: EmailStr
    mobileNumber: str
    areaOfResidence: str
    emergencyContactName: str
    relationship: str
    emergencyContactMobileNumber: str

class PaginatedRegistrations(BaseModel):
    total: int
    skip: int
    limit: int
    data: List[RegistrationResponse]

class PaginationRequest(BaseModel):
    skip: int = 0
    limit: int = 10

def get_db_connection():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE
    )

def clean_base64(b64_string: str) -> Tuple[str, str]:
    """Extract base64 data and extension from URI if present."""
    logger.debug(f"Cleaning base64 string of length: {len(b64_string)}")
    match = re.match(r"data:image/(?P<ext>\w+);base64,(?P<data>.+)", b64_string)
    if match:
        ext = match.group("ext")
        data = match.group("data")
        logger.debug(f"Found image extension: {ext}")
        return data, ext
    logger.debug("No URI prefix found, assuming plain base64")
    return b64_string, "png"

def format_date(raw_date: str) -> str:
    """Convert MRZ date format (YYMMDD) to ISO format (YYYY-MM-DD)."""
    try:
        if raw_date:
            return datetime.strptime(raw_date, "%y%m%d").strftime("%Y-%m-%d")
    except Exception as e:
        logger.warning(f"Failed to parse date '{raw_date}': {str(e)}")
    return None

def parse_kenyan_names(raw_name: str) -> Tuple[str, str]:
    """
    Parse Kenyan names from MRZ data with comprehensive cleaning.
    Handles cases where full name is in surname field with L placeholders.
    """
    logger.info(f"Starting name parsing for: '{raw_name}'")
    
    if not raw_name:
        logger.warning("Empty name input received")
        return "", ""
    
    # First clean: normalize whitespace and remove obvious placeholders
    cleaned = ' '.join(raw_name.split())
    cleaned = re.sub(r'\bL\b', '', cleaned)  # Remove standalone L's
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    logger.debug(f"After initial cleaning: '{cleaned}'")
    
    # Second clean: remove any remaining L patterns
    cleaned = re.sub(r'L+$', '', cleaned).strip()  # Trailing L's
    cleaned = re.sub(r'^L+', '', cleaned).strip()  # Leading L's
    logger.debug(f"After L removal: '{cleaned}'")
    
    if not cleaned:
        logger.warning("All content removed during cleaning")
        return "", ""
    
    # Split into components
    parts = [p for p in cleaned.split() if p and p != 'L']
    logger.debug(f"Name parts after splitting: {parts}")
    
    if not parts:
        logger.warning("No valid name parts found after splitting")
        return "", ""
    
    # Kenyan naming convention: last non-L part is surname
    surname = parts[-1]
    given_names = " ".join(parts[:-1]) if len(parts) > 1 else ""
    
    logger.info(f"Parsed names - Given: '{given_names}', Surname: '{surname}'")
    return given_names.strip(), surname.strip()

@app.post("/mrz/")
async def extract_mrz(request: ImageBase64Request):
    """Endpoint for extracting MRZ data from ID images."""
    try:
        # Step 1: Decode base64 and save temporary image
        b64_data = request.image_base64
        logger.info(f"Received base64 data of length: {len(b64_data)}")
        
        # Clean and validate base64 data
        image_data, ext = clean_base64(b64_data)
        logger.info(f"Cleaned base64 data, extracted extension: {ext}")
        
        try:
            image_data = base64.b64decode(image_data)
        except Exception as e:
            logger.error(f"Base64 decoding failed: {str(e)}")
            return {"status": "FAILURE", "error": "Invalid base64 data"}

        # Step 2: Extract MRZ
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
            tmp.write(image_data)
            tmp_path = tmp.name
        logger.info(f"Saved temporary image to: {tmp_path}")
        
        try:
            logger.debug("Starting MRZ extraction")
            mrz = read_mrz(tmp_path)
            
            if mrz is None:
                logger.error("No MRZ data found in image")
                return {"status": "FAILURE", "error": "No MRZ found"}
            
            data = mrz.to_dict()
            logger.debug(f"Raw MRZ data: {data}")
        except Exception as e:
            logger.error(f"MRZ extraction failed: {str(e)}", exc_info=True)
            return {"status": "FAILURE", "error": "MRZ extraction error"}
        finally:
            try:
                os.unlink(tmp_path)
                logger.debug("Temporary file removed")
            except:
                pass

        # Step 3: Parse names
        raw_name = data.get("surname", "") or data.get("names", "")
        logger.info(f"Raw name field from MRZ: '{raw_name}'")
        logger.info(f"Raw data :'{data}'")
        
        given_name, surname = parse_kenyan_names(raw_name)
        
        # Step 4: Prepare response
        response = {
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
            "status": "SUCCESS",
            "debug": {
                "raw_surname_field": raw_name,
                "name_components": raw_name.split() if raw_name else []
            } if logger.level == logging.DEBUG else None
        }
        
        logger.info("MRZ extraction completed successfully")
        return response

    except Exception as e:
        logger.critical(f"Unexpected error in MRZ extraction: {str(e)}", exc_info=True)
        return {"status": "FAILURE", "error": "Internal server error"}

@app.post("/register/")
async def register_user(data: RegistrationRequest):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Ensure table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS registrations (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fullName VARCHAR(255),
                email VARCHAR(255),
                mobileNumber VARCHAR(20),
                areaOfResidence VARCHAR(255),
                emergencyContactName VARCHAR(255),
                relationship VARCHAR(100),
                emergencyContactMobileNumber VARCHAR(20)
            )
        """)
        # Insert data
        cursor.execute("""
            INSERT INTO registrations (
                fullName, email, mobileNumber, areaOfResidence,
                emergencyContactName, relationship, emergencyContactMobileNumber
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            data.fullName,
            data.email,
            data.mobileNumber,
            data.areaOfResidence,
            data.emergencyContactName,
            data.relationship,
            data.emergencyContactMobileNumber
        ))
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"Registered user: {data.fullName}")
        return {"message": "Registration successful"}
    except Exception as e:
        logger.error(f"Registration failed: {e}")
        raise HTTPException(status_code=500, detail="Registration failed")

@app.post(
    "/registrations/",
    response_model=PaginatedRegistrations,
    summary="Get a paginated list of registrations (POST)",
    description="""
Returns a paginated list of registrations using a JSON body.

**Request Body:**
- `skip`: Number of records to skip (default: 0)
- `limit`: Maximum number of records to return (default: 10, max: 100)
"""
)
async def list_registrations_post(body: PaginationRequest):
    try:
        skip = body.skip
        limit = min(body.limit, 100)
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) as total FROM registrations")
        total = cursor.fetchone()["total"]
        cursor.execute(
            "SELECT * FROM registrations ORDER BY id DESC LIMIT %s OFFSET %s", (limit, skip)
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "data": rows
        }
    except Exception as e:
        logger.error(f"Error fetching registrations: {e}")
        raise HTTPException(status_code=500, detail="Could not fetch registrations")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)