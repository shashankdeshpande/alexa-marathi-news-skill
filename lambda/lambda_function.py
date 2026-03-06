# -*- coding: utf-8 -*-

import os
import re
import logging
import random
from datetime import datetime, timedelta, timezone
from ask_sdk_core.skill_builder import CustomSkillBuilder
from ask_sdk_core.api_client import DefaultApiClient
from ask_sdk_core.dispatch_components import AbstractExceptionHandler, AbstractRequestHandler, AbstractRequestInterceptor, AbstractResponseInterceptor

from ask_sdk_core.utils import is_request_type, is_intent_name
from ask_sdk_s3.adapter import S3Adapter 
from ask_sdk_model.interfaces.audioplayer import (
    PlayDirective, PlayBehavior, AudioItem, Stream, StopDirective, AudioItemMetadata
)
from ask_sdk_model.interfaces.display import Image as DisplayImage, ImageInstance
from dotenv import load_dotenv
load_dotenv()

import db_helper
import constants

# --- Configuration ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

db_helper.init_db()

try:
    s3_adapter = S3Adapter(bucket_name=os.environ.get("S3_PERSISTENCE_BUCKET"))
except Exception:
    logger.warning("S3_PERSISTENCE_BUCKET not set. Persistence will fail.")
    s3_adapter = None

# --- Logic ---

def generate_natural_alexa_ssml(published_at):
    """
    Generates SSML with time-of-day context based on published time (IST).
    :param published_at: datetime object or ISO string
    """
    # Parse if string, otherwise assume datetime
    if isinstance(published_at, str):
        try:
            dt = datetime.strptime(published_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            dt = datetime.fromisoformat(published_at)
    else:
        dt = published_at
    
    # Ensure dt is timezone-aware (UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    # Convert everything to IST for user-facing logic
    IST = timezone(timedelta(hours=5, minutes=30))
    dt_ist = dt.astimezone(IST)
    now_ist = datetime.now(IST)
    
    # Determine Time of Day (for the published time)
    hour = dt_ist.hour
    if 5 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 17:
        period = "afternoon"
    elif 17 <= hour < 21:
        period = "evening"
    else:
        period = "night"

    # Format time: "9 PM"
    floored_dt = dt_ist.replace(minute=0, second=0, microsecond=0)
    hour_str = floored_dt.strftime("%I").lstrip("0")
    am_pm = floored_dt.strftime("%p")
    bulletin_time = f"{hour_str} {am_pm}"

    # Generate Preamble based on date difference
    if dt_ist.date() == now_ist.date():
        # TODAY: "Here is your morning update from 9 AM."
        variations = [
            f"Here is your {period} update from {bulletin_time}.",
            f"Starting the {period} bulletin recorded at {bulletin_time}.",
            f"Here are the {period} headlines as of {bulletin_time}."
        ]
    else:
        # OLDER: "Here are 28th January headlines as of 9 PM."
        month = dt_ist.strftime("%B")
        day_int = int(dt_ist.strftime("%d"))
        
        if 11 <= (day_int % 100) <= 13:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day_int % 10, 'th')
            
        formatted_date = f"{day_int}{suffix} {month}"
        
        variations = [
            f"Here are {formatted_date} headlines as of {bulletin_time}.",
            f"These are the headlines from {formatted_date}, recorded at {bulletin_time}.",
            f"Playing the bulletin from {formatted_date}, as of {bulletin_time}."
        ]

    intro = random.choice(variations)
    return f"{intro} <break time='300ms'/>"

def _build_news_metadata(news_item):
    """
    Builds AudioItemMetadata with YouTube thumbnail for Echo Show display.
    """
    youtube_id = news_item.get('youtube_id')
    if not youtube_id:
        return None

    title = news_item.get('title', '')
    thumbnail_url = f"https://img.youtube.com/vi/{youtube_id}/hqdefault.jpg"
    art = DisplayImage(
        content_description=title,
        sources=[ImageInstance(url=thumbnail_url)]
    )
    # Format published_at as readable date
    subtitle = ''
    published_at = news_item.get('published_at')
    if published_at:
        try:
            subtitle = published_at.strftime("%d %B, %Y")
        except Exception as e:
            subtitle = str(published_at)
    return AudioItemMetadata(
        title=title,
        subtitle=subtitle,
        art=art,
        background_image=art
    )

def play_news(handler_input, offset=0, announce_title=False):
    """
    Orchestrates fetching news from DB and playing it.
    """
    # 1. Fetch recent news items (fallback logic)
    news_items = db_helper.get_recent_news(limit=3)
    
    # Extract User ID safe access
    user_id = "UNKNOWN_USER"
    try:
        # Try finding user_id in context or session
        if handler_input.request_envelope.context.system.user:
            user_id = handler_input.request_envelope.context.system.user.user_id
        elif handler_input.request_envelope.session and handler_input.request_envelope.session.user:
            user_id = handler_input.request_envelope.session.user.user_id
    except Exception as e:
        logger.warning(f"Could not extract user_id: {e}")

    if not news_items:
        logger.info("No news found in DB.")
        db_helper.log_user_activity(user_id=user_id, status="FAILED", error_message="No news found in DB")
        return handler_input.response_builder.speak(constants.NO_NEWS_FOUND).response

    valid_news_item = None
    skipped_items_count = 0
    
    # Iterate to find first valid audio
    for i, item in enumerate(news_items):
        audio_url = item.get('audio_url')
        db_id = item['id']
        title = item['title']
        
        # Validate Audio URL using helper
        if db_helper.validate_audio_url(audio_url):
            valid_news_item = item
            break # Found valid item
        else:
            logger.warning(f"Audio URL validation failed for {db_id}")
            skipped_items_count += 1
             
    if not valid_news_item:
        logger.error("All recent news items failed validation.")
        # Log failure for the top item as representative
        top_item = news_items[0]
        db_helper.log_user_activity(user_id=user_id, news_id=top_item['id'], video_title=top_item['title'], status="FAILED", error_message="All recent items invalid")
        return handler_input.response_builder.speak(constants.NO_NEWS_FOUND).response

    # Play the valid item
    news_item = valid_news_item
    db_id = news_item['id']
    title = news_item['title']
    audio_url = news_item.get('audio_url')
    
    logger.info(f"Playing news: {title} (ID: {db_id})")
    
    # Log Success or Partial Success
    if skipped_items_count == 0:
        db_helper.log_user_activity(user_id=user_id, news_id=db_id, video_title=title, status="SUCCESS")
    else:
        msg = f"Audio url is not valid, selected previous (skipped {skipped_items_count})"
        db_helper.log_user_activity(user_id=user_id, news_id=db_id, video_title=title, status="PARTIAL_SUCCESS", error_message=msg)
    
    # Speak preamble if requested (First time play)
    if announce_title:
        try:
            published_at = news_item.get('published_at')
            readable_preamble = generate_natural_alexa_ssml(published_at)
            handler_input.response_builder.speak(readable_preamble)
        except Exception as e:
            logger.error(f"Error generating preamble: {e}")
            handler_input.response_builder.speak(constants.PLAYING_NEWS_FALLBACK)

    # 4. Save State for Resume
    try:
        attr = handler_input.attributes_manager.persistent_attributes
        attr['current_token'] = db_id
        attr['current_offset'] = offset
        handler_input.attributes_manager.save_persistent_attributes()
    except Exception as e:
        logger.error(f"Failed to save persistent attributes: {e}")

    # 5. Build Metadata (thumbnail for Echo Show)
    metadata = _build_news_metadata(news_item)

    # 6. Build Response
    handler_input.response_builder.add_directive(
        PlayDirective(
            play_behavior=PlayBehavior.REPLACE_ALL,
            audio_item=AudioItem(
                stream=Stream(
                    token=db_id,
                    url=audio_url,
                    offset_in_milliseconds=offset,
                    expected_previous_token=None
                ),
                metadata=metadata
            )
        )
    )
    
    return handler_input.response_builder.response

# --- Handlers ---

class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        logger.info(f"Session started: {handler_input.request_envelope.session.session_id}")
        # Announce title on launch
        return play_news(handler_input, announce_title=True)

class PauseStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return (is_intent_name("AMAZON.StopIntent")(handler_input) or
                is_intent_name("AMAZON.PauseIntent")(handler_input) or
                is_intent_name("AMAZON.CancelIntent")(handler_input))

    def handle(self, handler_input):
        logger.info("Paused/Stopped playback.")
        handler_input.response_builder.add_directive(StopDirective())
        return handler_input.response_builder.response

class ResumeIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.ResumeIntent")(handler_input)

    def handle(self, handler_input):
        logger.info("Resuming playback.")
        attr = handler_input.attributes_manager.persistent_attributes
        offset = attr.get('current_offset', 0)
        return play_news(handler_input, offset=offset)

class EventsHandler(AbstractRequestHandler):
    """Handles AudioPlayer events."""
    def can_handle(self, handler_input):
        return (is_request_type("AudioPlayer.PlaybackStopped")(handler_input) or
                is_request_type("AudioPlayer.PlaybackFinished")(handler_input) or
                is_request_type("AudioPlayer.PlaybackStarted")(handler_input) or
                is_request_type("AudioPlayer.PlaybackFailed")(handler_input))

    def handle(self, handler_input):
        request_type = handler_input.request_envelope.request.object_type
        
        if request_type == "AudioPlayer.PlaybackStopped":
            offset = handler_input.request_envelope.request.offset_in_milliseconds
            attr = handler_input.attributes_manager.persistent_attributes
            attr['current_offset'] = offset
            handler_input.attributes_manager.save_persistent_attributes()
            
        elif request_type == "AudioPlayer.PlaybackFinished":
            attr = handler_input.attributes_manager.persistent_attributes
            attr['current_offset'] = 0
            handler_input.attributes_manager.save_persistent_attributes()
            
        elif request_type == "AudioPlayer.PlaybackFailed":
            logger.error(f"Playback Failed: {handler_input.request_envelope.request.error}")

        return handler_input.response_builder.response

class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        msg = constants.HELP_MESSAGE
        return handler_input.response_builder.speak(msg).ask(msg).response

class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        msg = constants.FALLBACK_MESSAGE
        return handler_input.response_builder.speak(msg).ask(msg).response

class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        logger.info(f"Session ended: {handler_input.request_envelope.request.reason}")
        return handler_input.response_builder.response

class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.error(f"Unhandled Exception: {exception}", exc_info=True)
        msg = constants.ERROR_MESSAGE
        return handler_input.response_builder.speak(msg).response

class RequestLogger(AbstractRequestInterceptor):
    """Log the alexa requests."""
    def process(self, handler_input):
        # type: (HandlerInput) -> None
        logger.debug("Alexa Request: {}".format(
            handler_input.request_envelope.request))

class ResponseLogger(AbstractResponseInterceptor):
    """Log the alexa responses."""
    def process(self, handler_input, response):
        # type: (HandlerInput, Response) -> None
        logger.debug("Alexa Response: {}".format(response))

# --- Builder ---

sb = CustomSkillBuilder(persistence_adapter=s3_adapter, api_client=DefaultApiClient())

sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PauseStopIntentHandler())
sb.add_request_handler(ResumeIntentHandler())
sb.add_request_handler(EventsHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())

sb.add_exception_handler(CatchAllExceptionHandler())

sb.add_global_request_interceptor(RequestLogger())
sb.add_global_response_interceptor(ResponseLogger())

lambda_handler = sb.lambda_handler()
