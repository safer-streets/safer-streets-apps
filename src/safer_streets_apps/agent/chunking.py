"""
Uses Gemini to split pdfs into text chunks
Can return invalid output for large pdf files, may need to split them beforehand
"""

import json
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import RootModel

load_dotenv()


class Chunks(RootModel[list[str]]): ...


def main(file: Path) -> None:
    client = (
        genai.Client()
    )

    def gemini_chunk_pdf(pdf_path: Path, min_chars: int = 700, max_chars: int = 1200, overlap_hint: int = 0) -> Chunks | None:
        """
        Upload the PDF via Files API and ask Gemini to split it into a JSON array of chunk strings.
        Constraints are hints; Gemini uses semantic boundaries like headings/sections.
        """

        # use cached result if available
        parsed_file = pdf_path.with_suffix(".chunked.json")

        if parsed_file.is_file():
            with parsed_file.open() as fd:
                return Chunks(json.load(fd))

        pdf_file = client.files.upload(file=pdf_path)

        system_instructions = (
            "You are a document chunker. Split the attached PDF into a list of coherent text chunks. "
            "Each chunk should be self-contained, preserve key headings/subheadings when present, "
            f"and be between {min_chars} and {max_chars} characters when possible. "
            "Prefer splitting at semantic boundaries (section titles, paragraphs, bullet lists). "
            "Return only the array of strings as per the response schema, with no additional keys or metadata. "
            "Do not include page numbers unless they appear in the text itself. "
        )
        if overlap_hint > 0:
            system_instructions += (
                f" If it is natural to do so, gently allow small overlaps (~{overlap_hint} chars) "
                "for continuity; otherwise skip overlap."
            )

        # Ask Gemini to return pure JSON. If your SDK version supports it, you can enforce JSON via response_mime_type.
        # To keep it portable, we'll rely on instructions and parse resp.text as JSON.  [2](https://ai.google.dev/gemini-api/docs/document-processing)
        stream = client.models.generate_content_stream(
            model="gemini-2.5-flash",  # fast & multimodal for document tasks  [2](https://ai.google.dev/gemini-api/docs/document-processing)
            contents=[system_instructions, pdf_file],
            config=types.GenerateContentConfig(
                temperature=0, response_mime_type="application/json", response_schema=Chunks
            ),
        )

        resp = ""
        for part in stream:
            resp += part.text
            print(len(resp))

        try:
            chunks = Chunks.model_validate_json(resp)
            with parsed_file.open("w") as fd:
                fd.write(chunks.model_dump_json(indent=2))
        except:  # noqa E722
            print("output parsing error, dumping full response to raw.txt")
            with Path("raw.txt").open("w") as fd:
                fd.write(resp)
        else:
            return chunks

    chunks = gemini_chunk_pdf(file).model_dump()

    print(
        f"{len(chunks)} chunks, min/mean/max chars = {min(len(c) for c in chunks)} / {sum(len(c) for c in chunks) / len(chunks):.1f} / {max(len(c) for c in chunks)}"
    )


if __name__ == "__main__":
    # main(Path("./m2_series_English.pdf"))
    for file in Path(".").glob("55stepsuk_0_0.part*.pdf"):
        main(file)

    chunks = []
    for file in Path(".").glob("55stepsuk_0_0.part*.json"):
        chunks.extend(json.load(file.open()))
    with Path("55stepsuk_0_0.chunked.json").open("w") as fd:
        json.dump(chunks, fd, indent=2)
