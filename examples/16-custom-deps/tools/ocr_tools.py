"""OCR tools — extract text from images and PDFs."""

from ninetrix import Tool


@Tool
def extract_text(image_path: str, language: str = "eng") -> str:
    """Extract text from an image using Tesseract OCR.

    Args:
        image_path: Path to the image file (PNG, JPG, TIFF, etc.).
        language: OCR language code (default: eng). Use 'heb' for Hebrew, etc.
    """
    import pytesseract
    from PIL import Image

    image = Image.open(image_path)
    text = pytesseract.image_to_string(image, lang=language)
    return text.strip()


@Tool
def analyze_pdf(pdf_path: str, pages: str = "all") -> str:
    """Extract text from a PDF file.

    Args:
        pdf_path: Path to the PDF file.
        pages: Page range to extract. 'all' for all pages, '1-5' for specific range.
    """
    import fitz  # pymupdf

    doc = fitz.open(pdf_path)
    results = []

    if pages == "all":
        page_range = range(len(doc))
    else:
        start, end = pages.split("-")
        page_range = range(int(start) - 1, int(end))

    for i in page_range:
        page = doc[i]
        text = page.get_text()
        if text.strip():
            results.append(f"--- Page {i + 1} ---\n{text.strip()}")

    doc.close()
    return "\n\n".join(results) if results else "(no text found)"
