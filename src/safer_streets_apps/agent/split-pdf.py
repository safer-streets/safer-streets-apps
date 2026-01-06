from pathlib import Path

from PyPDF2 import PdfReader, PdfWriter


def split(file: Path) -> None:
    doc = PdfReader(file)
    output = PdfWriter()
    for p, page in enumerate(doc.pages, start=1):
        if p % 10 == 0:
            output_file = file.with_suffix(f".part{p // 10}.pdf")
            with output_file.open("wb") as fd:
                output.write(fd)
            print(output_file)
            output = PdfWriter()
        output.add_page(page)
    # dump remainder
    output_file = file.with_suffix(f".part{p // 10 + 1}.pdf")
    with output_file.open("wb") as fd:
        output.write(fd)
    print(output_file)


if __name__ == "__main__":
    file = Path("55stepsuk_0_0.pdf")
    split(file)
