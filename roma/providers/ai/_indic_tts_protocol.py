"""Small stdlib-only protocol shared by the app and isolated Indic runtime."""

import json

MAX_FRAME_BYTES = 65536
RESIDENT_PROFILE = "resident-kaiser-cosine15ms-v1"


def encode_frame(payload):
    frame = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
    if len(frame) > MAX_FRAME_BYTES:
        raise ValueError("Indic-TTS protocol frame too large")
    return frame


def decode_frame(frame):
    if not frame.endswith(b"\n") or len(frame) > MAX_FRAME_BYTES:
        raise ValueError("Invalid Indic-TTS protocol frame")
    payload = json.loads(frame)
    if type(payload) is not dict or type(payload.get("id")) is not int or payload["id"] <= 0:
        raise ValueError("Invalid Indic-TTS protocol request ID")
    epoch = payload.get("turn_epoch")
    if "turn_epoch" not in payload or (
        epoch is not None and (type(epoch) is not int or epoch < 0)
    ):
        raise ValueError("Invalid Indic-TTS protocol epoch")
    return payload
