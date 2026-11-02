"""Reads text from a picture held in memory, on this machine. Nothing is written to disk or sent anywhere."""
from .browser_errors import ElementError
from .i18n import t

_reader = None


def read_text(image):
    global _reader
    if _reader is None:
        # Loaded on first use, as only this block needs it and it takes a moment.
        try:
            import ddddocr

            _reader = ddddocr.DdddOcr(show_ad=False)
        except Exception as error:
            raise ElementError(t("OCR could not start: {error}", error=str(error).splitlines()[0] if str(error) else type(error).__name__)) from error
    return _reader.classification(image).strip()
