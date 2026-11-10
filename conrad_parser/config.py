import os

from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://www.conrad.de"
SEARCH_URL = BASE_URL + "/de/search.html?search={query}"
ACCOUNT_URL = BASE_URL + "/de/account.html"

EMAIL = os.getenv("CONRAD_EMAIL", "")
PASSWORD = os.getenv("CONRAD_PASSWORD", "")

PROFILE_DIR = os.getenv("BROWSER_PROFILE_DIR", ".browser_profile")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")

# pause between product pages, seconds
MIN_DELAY = float(os.getenv("MIN_DELAY", "2"))
MAX_DELAY = float(os.getenv("MAX_DELAY", "5"))
