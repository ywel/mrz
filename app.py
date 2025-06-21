import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

from passporteye import read_mrz
import json

image_path = "download.jpg"

mrz = read_mrz(image_path)

if mrz is None:
    print("No MRZ found in the image.")
else:
    mrz_dict = mrz.to_dict()
    # PassportEye wraps parsed data under the 'mrz' key
    parsed = mrz_dict.get('mrz', mrz_dict)  # fallback if no wrapping
    print(json.dumps(parsed, indent=4))
