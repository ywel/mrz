from fastapi import FastAPI
from pydantic import BaseModel
from fastmrz import FastMRZ
import base64
import tempfile
import logging

app = FastAPI()
fast_mrz = FastMRZ()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ImageBase64Request(BaseModel):
    image_base64: str

@app.post("/mrz/")
async def extract_mrz(request: ImageBase64Request):
    try:
        # Decode base64 image
        logger.info("Decoding base64 image")
        image_data = base64.b64decode(request.image_base64)
        # Save to a temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(image_data)
            tmp_path = tmp.name
        logger.info(f"Image saved to temporary file: {tmp_path}")

        # Extract MRZ details
        logger.info("Extracting MRZ details")
        passport_mrz = fast_mrz.get_details(tmp_path, include_checkdigit=False)
        logger.info("MRZ extraction successful")
        return passport_mrz
    except Exception as e:
        logger.error(f"Error during MRZ extraction: {e}")
        return {"error": str(e)}

# To run: uvicorn main:app --reload