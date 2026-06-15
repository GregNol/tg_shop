"""Generate a QR code PNG (in-memory) for a subscription URL."""

import io
import logging

logger = logging.getLogger(__name__)


def make_qr_png(data: str) -> bytes | None:
    """Render `data` as a QR-code PNG and return raw bytes, or None on failure."""
    try:
        import qrcode

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        logger.exception("Failed to generate QR code")
        return None
