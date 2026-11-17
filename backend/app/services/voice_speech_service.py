import os

from app.services.provider_registry import get_provider_registry

class VoiceSpeechService:

    @staticmethod
    async def generate_audio(
        text: str,
        filename: str = "ai_response.mp3",
        speech_service=None
    ):
        if speech_service is None:
            speech_service = get_provider_registry().get_tts_processing_service()
        audio_data = await speech_service.convert_text_to_audio(text)
        os.makedirs("static/audio", exist_ok=True)

        output_path = os.path.join("static/audio", filename)
        with open(output_path, "wb") as audio_file:
            audio_file.write(audio_data)

        return output_path