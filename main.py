from fastapi import FastAPI, HTTPException, Query, Request, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, EmailStr, constr, field_validator, Field
import mysql.connector
import logging
import os
import base64
import tempfile
import re
from passporteye import read_mrz
from datetime import datetime
from typing import List, Optional, Tuple
from functools import wraps
from time import time
from collections import defaultdict
from fastapi import Request, HTTPException
import secrets

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
    image_base64: constr(min_length=100, max_length=10_000_000, strip_whitespace=True)

    @field_validator("image_base64")
    @classmethod
    def validate_base64(cls, v):
        b64_data = v
        if b64_data.startswith("data:image/"):
            b64_data = b64_data.split(",", 1)[-1]
        try:
            base64.b64decode(b64_data, validate=True)
        except Exception:
            raise ValueError("Invalid base64-encoded image data")
        return v

class RegistrationRequest(BaseModel):
    fullName: constr(min_length=2, max_length=255, strip_whitespace=True)
    email: EmailStr
    mobileNumber: constr(regex=r"^\d{10,15}$")
    areaOfResidence: constr(min_length=2, max_length=255, strip_whitespace=True)
    emergencyContactName: constr(min_length=2, max_length=255, strip_whitespace=True)
    relationship: constr(min_length=2, max_length=100, strip_whitespace=True)
    emergencyContactMobileNumber: constr(regex=r"^\d{10,15}$")

    @field_validator("fullName", "areaOfResidence", "emergencyContactName", "relationship")
    @classmethod
    def no_special_chars(cls, v):
        if not re.match(r"^[\w\s\-\.\']+$", v):
            raise ValueError("Field contains invalid characters")
        return v

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

    @field_validator("skip")
    @classmethod
    def skip_non_negative(cls, v):
        if v < 0:
            raise ValueError("skip must be non-negative")
        return v

    @field_validator("limit")
    @classmethod
    def limit_range(cls, v):
        if not (1 <= v <= 100):
            raise ValueError("limit must be between 1 and 100")
        return v

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

security = HTTPBasic()
BASIC_AUTH_USERNAME = os.getenv("BASIC_AUTH_USERNAME", "g86EGP0CMY")
BASIC_AUTH_PASSWORD = os.getenv("BASIC_AUTH_PASSWORD", "gz2MR9vZfq4xXWPouHxqRsL5ckbymCjM")

def verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, BASIC_AUTH_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, BASIC_AUTH_PASSWORD)
    if not (correct_username and correct_password):
        logger.warning(f"Failed login attempt for user: {credentials.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

@app.post("/mrz/")
async def extract_mrz(
    request: Request,
    body: ImageBase64Request,
    username: str = Depends(verify_basic_auth)
):
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
async def register_user(
    request: Request,
    data: RegistrationRequest,
    username: str = Depends(verify_basic_auth)
):
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
async def list_registrations_post(
    request: Request,
    body: PaginationRequest,
    username: str = Depends(verify_basic_auth)
):
    try:
        skip = body.skip
        limit = min(body.limit, 100)
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) as total FROM registrations")
        total = cursor.fetchone()["total"]
        cursor.execute(
            "SELECT * FROM registrations WHERE clicked=0 ORDER BY id ASC LIMIT %s OFFSET %s", (limit, skip)
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

from pydantic import BaseModel, Field

class UpdateClickedRequest(BaseModel):
    id: int = Field(..., gt=0, description="ID of the registration to update")

@app.post("/registrations/update_clicked/")
async def update_clicked_column(
    request: Request,
    body: UpdateClickedRequest,
    username: str = Depends(verify_basic_auth)
):
    rate_limiter(request)
    ip = request.client.host
    logger.info(f"Update 'clicked' column request from IP: {ip}, user: {username}, id: {body.id}")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Ensure the 'clicked' column exists
        cursor.execute("""
            ALTER TABLE registrations
            ADD COLUMN IF NOT EXISTS clicked INT DEFAULT 0
        """)
        # Update the clicked column for the given id
        cursor.execute(
            "UPDATE registrations SET clicked = 10 WHERE id = %s", (body.id,)
        )
        conn.commit()
        affected = cursor.rowcount
        cursor.close()
        conn.close()
        if affected == 0:
            logger.warning(f"No registration found with id {body.id}")
            return {"status": "error", "message": f"No registration found with id {body.id}"}
        logger.info(f"Updated 'clicked' column for id {body.id}")
        return {"status": "success", "message": f"Clicked column updated for id {body.id}"}
    except Exception as e:
        logger.error(f"Error updating clicked column: {e}")
        raise HTTPException(status_code=500, detail="Failed to update clicked column")

# Rate limiting setup (simple in-memory, per-IP)
RATE_LIMIT = int(os.getenv("RATE_LIMIT", 30))  # requests
RATE_PERIOD = int(os.getenv("RATE_PERIOD", 60))  # seconds

ip_request_times = defaultdict(list)

def rate_limiter(request: Request):
    ip = request.client.host
    now = time()
    window_start = now - RATE_PERIOD
    # Remove timestamps outside the window
    ip_request_times[ip] = [t for t in ip_request_times[ip] if t > window_start]
    if len(ip_request_times[ip]) >= RATE_LIMIT:
        logger.warning(f"Rate limit exceeded for IP: {ip}")
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {RATE_LIMIT} requests per {RATE_PERIOD} seconds"
        )
    ip_request_times[ip].append(now)
    logger.info(f"Request from IP: {ip} - {len(ip_request_times[ip])} requests in window")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)