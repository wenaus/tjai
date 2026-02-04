"""Voice processing: Whisper STT and OpenAI TTS."""

import logging
import tempfile
from pathlib import Path

from openai import OpenAI

from .config import Config

logger = logging.getLogger(__name__)

# TTS voice options: alloy, echo, fable, onyx, nova, shimmer
TTS_VOICE = "nova"
TTS_MODEL = "tts-1"  # or "tts-1-hd" for higher quality


def get_openai_client() -> OpenAI:
    """Get OpenAI client."""
    if not Config.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not configured")
    return OpenAI(api_key=Config.OPENAI_API_KEY)


def transcribe_audio(audio_path: str | Path) -> str:
    """
    Transcribe audio file to text using Whisper API.

    Args:
        audio_path: Path to audio file (OGG, MP3, WAV, etc.)

    Returns:
        Transcribed text
    """
    client = get_openai_client()

    with open(audio_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )

    return transcript.text


def text_to_speech(text: str, output_path: str | Path = None) -> Path:
    """
    Convert text to speech using OpenAI TTS API.

    Args:
        text: Text to convert to speech
        output_path: Optional output path. If None, creates temp file.

    Returns:
        Path to the generated audio file (OGG format for Telegram)
    """
    client = get_openai_client()

    if output_path is None:
        # Create temp file with .ogg extension for Telegram voice
        tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        output_path = Path(tmp.name)
        tmp.close()
    else:
        output_path = Path(output_path)

    response = client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=text,
        response_format="opus",  # OGG Opus for Telegram voice messages
    )

    response.stream_to_file(output_path)
    logger.info(f"Generated TTS audio: {output_path}")

    return output_path


async def transcribe_telegram_voice(voice_file) -> str:
    """
    Download and transcribe a Telegram voice message.

    Args:
        voice_file: Telegram Voice or Audio file object

    Returns:
        Transcribed text
    """
    # Download to temp file
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    await voice_file.download_to_drive(tmp_path)
    logger.info(f"Downloaded voice message to {tmp_path}")

    try:
        text = transcribe_audio(tmp_path)
        logger.info(f"Transcribed: {text[:50]}...")
        return text
    finally:
        # Clean up temp file
        tmp_path.unlink(missing_ok=True)
