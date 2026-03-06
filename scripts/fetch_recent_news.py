import os
import re
import sys
import logging
import requests
import feedparser
import time
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta, timezone
from dateutil import parser
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("fetch_news")

# Import helpers from lambda directory
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'lambda'))
try:
    import db_helper
except ImportError as e:
    logger.error(f"Error importing helpers: {e}")
    sys.exit(1)

# Configuration
RSS_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=UCcbCOCo2JeLRK48puKro1pQ"
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "yt-api.p.rapidapi.com"
MAX_API_CALLS_PER_DAY = 8
CRON_CADENCE_MINUTES = 15

def check_prerequisites():
    if not RAPIDAPI_KEY:
         raise EnvironmentError("RAPIDAPI_KEY not set")
    try:
        conn = db_helper.get_db_connection()
        conn.close()
    except Exception as e:
        raise ConnectionError(f"Database check failed: {e}")

def fetch_recent_headlines_video():
    """Returns the latest 'Headlines' video entry from RSS feed."""
    logger.info("Parsing RSS feed...")
    feed = feedparser.parse(RSS_FEED_URL)
    if not feed.entries:
        return None
        
    entries = sorted(feed.entries, key=lambda x: x.published_parsed, reverse=True)
    for entry in entries:
        if entry.title.lower().startswith("headlines"):
            return entry
    return None

def get_audio_stream_url(video_id):
    """Fetches video info and returns best audio/mp4 stream URL from RapidAPI."""
    logger.info(f"Fetching stream info for {video_id}...")
    url = f"https://{RAPIDAPI_HOST}/dl"
    headers = {
        "x-rapidapi-host": RAPIDAPI_HOST,
        "x-rapidapi-key": RAPIDAPI_KEY
    }
    
    res = requests.get(url, headers=headers, params={"id": video_id, "cgeo": "IN"})
    res.raise_for_status()
    data = res.json()
    
    # Filter for audio/mp4, fallback to video/mp4
    aac_streams = [f for f in data.get("adaptiveFormats", []) if "audio/mp4" in f.get("mimeType", "")]
    if aac_streams:
        logger.info("Using audio/mp4 stream.")
    else:
        logger.warning("No audio/mp4 stream found. Falling back to video/mp4...")
        aac_streams = [f for f in data.get("adaptiveFormats", []) if "video/mp4" in f.get("mimeType", "")]
        if not aac_streams:
            raise RuntimeError("No audio/mp4 or video/mp4 stream found.")
        logger.info("Using video/mp4 stream as fallback.")

    best = max(aac_streams, key=lambda x: int(x.get("bitrate", 0)))
    duration = int(best.get("approxDurationMs", 0)) // 1000
    
    return best.get("url"), duration

def check_expiration(url):
    """
    Checks if the URL is expiring
    Returns True if expiring or expired, False otherwise.
    """
    try:
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        expire_timestamp = query_params.get('expire', [None])[0]
        
        if not expire_timestamp:
            logger.warning("No 'expire' parameter found in URL.")
            return True # Assuming expired/invalid if not found, to be safe

        expire_time = int(expire_timestamp)
        current_time = int(time.time())
        buffer_seconds = CRON_CADENCE_MINUTES * 60

        if current_time + buffer_seconds > expire_time:
            logger.info(f"URL is expiring soon (expire: {expire_time}, now: {current_time}). Needed refresh.")
            return True
        
        return False
    except Exception as e:
        logger.error(f"Error checking expiration: {e}")
        return True # Default to expire on error to force refresh

def update_readme(title, published_dt, video_id):
    """Updates README.md with the latest news info."""
    readme_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'README.md'))
    logger.info(f"Updated README path: {readme_path}")
    
    # Convert to IST for display
    IST = timezone(timedelta(hours=5, minutes=30))
    if published_dt.tzinfo is None:
        published_dt = published_dt.replace(tzinfo=timezone.utc)
    dt_ist = published_dt.astimezone(IST)
    time_str = dt_ist.strftime("%d %b %Y %I:%M %p IST")
    
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    
    new_content = f"[![Watch on YouTube]({thumbnail_url})]({video_url})  \n**[{title}]({video_url})**  \n📅 {time_str}"
    
    try:
        if not os.path.exists(readme_path):
             logger.error(f"README file not found at: {readme_path}")
             return

        with open(readme_path, 'r') as f:
            content = f.read()
            
        pattern = r"(<!-- LATEST_NEWS_START -->)(.*?)(<!-- LATEST_NEWS_END -->)"
        
        if not re.search(pattern, content, flags=re.DOTALL):
            logger.error("Marker tags not found in README.md")
            return

        # Use \3 for the end tag (Group 3). 
        # Replaces old content (Group 2) with new_content.
        replacement = f"\\1\n{new_content}\n\\3"
        
        updated_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
        
        with open(readme_path, 'w') as f:
            f.write(updated_content)
            
        logger.info("README.md updated.")
    except Exception as e:
        logger.warning(f"Failed to update README.md: {e}")

def main():
    try:
        check_prerequisites()
        
        # Initialize DB (and migrate schema if needed)
        db_helper.init_db()

        # Rate Limiting Check (Max 8 calls per day)
        if db_helper.get_today_api_call_count() >= MAX_API_CALLS_PER_DAY:
            logger.warning("Daily limit of 8 RapidAPI calls reached. Skipping update/insert.")
            return
        
        entry = fetch_recent_headlines_video()
        if not entry:
            logger.info("No 'Headlines' video found. Skipping.")
            return

        video_id = entry.yt_videoid
        
        # Parse published_dt datetime string
        published_dt = parser.parse(entry.published)
        if published_dt.tzinfo is None:
            published_dt = published_dt.replace(tzinfo=timezone.utc)
        else:
            published_dt = published_dt.astimezone(timezone.utc)

        logger.info(f"Found: {entry.title} ({video_id})")

        # Check DB existence
        existing_item = db_helper.get_news_item_by_youtube_id(video_id)
        
        if existing_item:
            # Same video, check expiration
            if not check_expiration(existing_item.get('audio_url')):
                logger.info("Video already exists with valid URL. Skipping.")
                return
            else:
                logger.info("Existing URL expired. Fetching fresh URL...")
        else:
            # New video
            logger.info("New video found. Fetching fresh URL...")

        # Get URL from RapidAPI
        audio_url, duration = get_audio_stream_url(video_id)
        
        # Log the call
        db_helper.log_rapid_api_call()
        
        logger.info("Inserting news item into Database...")
        
        # Generate inserted_at in UTC
        inserted_at = datetime.now(timezone.utc)
        
        news_id = db_helper.insert_news_item(
             title=entry.title,
             audio_url=audio_url,
             youtube_id=video_id,
             duration_seconds=duration,
             published_at=published_dt,
             inserted_at=inserted_at
        )
        
        if news_id:
            logger.info("Success.")
            update_readme(entry.title, published_dt, video_id)
            
    except Exception as e:
        logger.error(f"Job Failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()