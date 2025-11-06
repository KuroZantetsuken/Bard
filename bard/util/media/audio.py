import base64
import io
import logging
from typing import Tuple

import numpy as np
import soundfile

logger = logging.getLogger("Bard")

DEFAULT_WAVEFORM = "FzYACgAAAAAAACQAAAAAAAA="


def get_audio_duration_and_waveform(
    audio_bytes: bytes, max_waveform_points: int = 100
) -> Tuple[float, str]:
    """
    Calculates the audio duration from OGG Opus bytes and generates a base64 encoded waveform string.
    The waveform is a series of 0-255 values representing audio amplitude.

    Args:
        audio_bytes: The audio data in OGG Opus format.
        max_waveform_points: The maximum number of points to represent the waveform.

    Returns:
        A tuple containing the duration of the audio in seconds and the base64 encoded waveform string.
    """

    try:
        with io.BytesIO(audio_bytes) as audio_io:
            audio_data, samplerate = soundfile.read(audio_io)

        if audio_data is None or samplerate is None:
            logger.error("soundfile.read returned None for audio_data or samplerate.")
            return 1.0, DEFAULT_WAVEFORM

        duration_secs = len(audio_data) / float(samplerate)
        mono_audio_data = (
            np.mean(audio_data, axis=1) if audio_data.ndim > 1 else audio_data
        )
        num_samples = len(mono_audio_data)
        if num_samples == 0:
            return duration_secs, DEFAULT_WAVEFORM
        if not np.issubdtype(mono_audio_data.dtype, np.floating):
            mono_audio_data = mono_audio_data.astype(np.float32)
        max_abs_val = np.max(np.abs(mono_audio_data))
        if max_abs_val > 1.0:
            mono_audio_data = mono_audio_data / max_abs_val
        step = max(1, num_samples // max_waveform_points)
        waveform_raw_bytes = bytearray()
        for i in range(0, num_samples, step):
            chunk = mono_audio_data[i : i + step]
            if len(chunk) == 0:
                continue
            rms_amplitude = np.sqrt(np.mean(chunk**2))
            scaled_value = int(min(max(0, rms_amplitude * 3.0), 1.0) * 99)
            waveform_raw_bytes.append(scaled_value)
        if not waveform_raw_bytes:
            return duration_secs, DEFAULT_WAVEFORM
        waveform_b64 = base64.b64encode(waveform_raw_bytes).decode("utf-8")
        return duration_secs, waveform_b64
    except Exception as e:
        logger.error(
            f"Error getting duration/waveform from audio bytes: {e}.",
            exc_info=True,
        )
        try:
            with io.BytesIO(audio_bytes) as audio_io_fallback:
                info = soundfile.info(audio_io_fallback)
            return info.duration, DEFAULT_WAVEFORM
        except Exception as e_info:
            logger.error(
                f"Fallback to get duration from audio bytes also failed. Error: {e_info}",
                exc_info=True,
            )
            return 1.0, DEFAULT_WAVEFORM
