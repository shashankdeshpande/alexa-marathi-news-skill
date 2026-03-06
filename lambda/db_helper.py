# -*- coding: utf-8 -*-
import os
import logging
import psycopg2
import requests
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)
db_schema = os.environ.get("DB_SCHEMA")

def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST"),
            database=os.environ.get("DB_NAME"),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASS"),
            port=os.environ.get("DB_PORT"),
        )
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

def init_db():
    """Creates the news_items table if it does not exist."""
    
    # User will delete table manually, so we just define the target schema.
    create_table_query = """
    CREATE TABLE IF NOT EXISTS "{}".news_items (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        audio_url TEXT,
        youtube_id TEXT,
        duration_seconds INTEGER DEFAULT 0,
        published_at TIMESTAMP WITH TIME ZONE,
        inserted_at TIMESTAMP WITH TIME ZONE
    );

    CREATE TABLE IF NOT EXISTS "{}".rapid_api_logs (
        id SERIAL PRIMARY KEY,
        called_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS "{}".user_activity_logs (
        id SERIAL PRIMARY KEY,
        user_id TEXT,
        news_id INTEGER,
        video_title TEXT,
        status TEXT,
        activity_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        error_message TEXT
    );
    """.format(db_schema, db_schema, db_schema)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(create_table_query)
        conn.commit()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
    finally:
        if conn:
            conn.close()

def get_latest_news():
    query = """
    SELECT id, title, audio_url, youtube_id, duration_seconds, published_at
    FROM "{}".news_items
    ORDER BY published_at DESC, id DESC
    LIMIT 1;
    """.format(db_schema)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            result = cur.fetchone()
            
        if result:
            logger.info(f"Fetched latest news: {result.get('title')} (ID: {result['id']})")
            result['id'] = str(result['id']) 
            return dict(result)
        else:
            logger.info("No news items found in the database.")
            return None
            
    except Exception as e:
        logger.error(f"Error fetching latest news: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_recent_news(limit=3):
    """Fetches the top N latest news items."""
    query = """
    SELECT id, title, audio_url, youtube_id, duration_seconds, published_at
    FROM "{}".news_items
    ORDER BY published_at DESC, id DESC
    LIMIT %s;
    """.format(db_schema)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (limit,))
            results = cur.fetchall()
            
        if results:
            logger.info(f"Fetched {len(results)} recent news items.")
            # Convert IDs to string and return list of dicts
            output = []
            for res in results:
                d = dict(res)
                d['id'] = str(d['id'])
                output.append(d)
            return output
        else:
            logger.info("No news items found in the database.")
            return []
            
    except Exception as e:
        logger.error(f"Error fetching recent news: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_news_item_by_youtube_id(youtube_id):
    """Checks if a news item exists by its YouTube ID. 
       Returns the LATEST inserted record for this youtube_id (highest ID).
    """
    # Use triple quotes or single quotes to avoid conflict with double quotes in query
    query = 'SELECT id, audio_url FROM "{}".news_items WHERE youtube_id = %s ORDER BY id DESC LIMIT 1;'.format(db_schema)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (youtube_id,))
            result = cur.fetchone()
        
        if result:
            return dict(result)
        return None
    except Exception as e:
        logger.error(f"Error checking news by youtube_id: {e}")
        return None
    finally:
        if conn:
            conn.close()

def insert_news_item(title, audio_url, youtube_id, duration_seconds=0, published_at=None, inserted_at=None):
    """Inserts a new news item into the database."""
    query = """
    INSERT INTO "{}".news_items (title, audio_url, youtube_id, duration_seconds, published_at, inserted_at)
    VALUES (%s, %s, %s, %s, %s, %s)
    RETURNING id;
    """.format(db_schema)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(query, (title, audio_url, youtube_id, duration_seconds, published_at, inserted_at))
            new_id = cur.fetchone()[0]
        conn.commit()
        logger.info(f"Inserted news item: {title} (ID: {new_id})")
        return new_id
    except Exception as e:
        logger.error(f"Error inserting news item: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()

def get_today_api_call_count():
    """Returns the number of RapidAPI calls made today (UTC)."""
    # We compare dates in UTC (default DB timezone usually, or explicit timezone).
    # Since we removed 'AT TIME ZONE', we assume standard UTC comparison.
    
    query = """
    SELECT COUNT(*) 
    FROM "{}".rapid_api_logs 
    WHERE (called_at)::date = CURRENT_DATE;
    """.format(db_schema)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(query)
            count = cur.fetchone()[0]
        return count
    except Exception as e:
        logger.error(f"Error counting daily API calls: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def log_rapid_api_call():
    """Logs a RapidAPI call to the database."""
    query = 'INSERT INTO "{}".rapid_api_logs (called_at) VALUES (CURRENT_TIMESTAMP);'.format(db_schema)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(query)
        conn.commit()
        logger.info("Logged RapidAPI call.")
    except Exception as e:
        logger.error(f"Error logging RapidAPI call: {e}")
    finally:
        if conn:
            conn.close()

def log_user_activity(user_id, news_id=None, video_title=None, status="SUCCESS", error_message=None):
    """Logs user activity (play request) to the database."""
    query = """
    INSERT INTO "{}".user_activity_logs (user_id, news_id, video_title, status, error_message, activity_time)
    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP);
    """.format(db_schema)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(query, (user_id, news_id, video_title, status, error_message))
        conn.commit()
        logger.info(f"Logged user activity for user: {user_id}, status: {status}")
    except Exception as e:
        logger.error(f"Error logging user activity: {e}")
    finally:
        if conn:
            conn.close()

def validate_audio_url(url):
    """Checks if the audio URL is valid (HTTP 200-399)."""
    if not url:
        return False
    try:
        response = requests.head(url, timeout=3)
        return response.status_code < 400
    except Exception as e:
        logger.warning(f"Audio URL validation failed for {url}: {e}")
        return False