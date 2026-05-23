"""Debug: что именно fitz возвращает из тестового PDF."""
import io, fitz

doc = fitz.open()
page1 = doc.new_page()
page1.insert_text((50, 50), "ДОГОВОР АРЕНДЫ № 123/2026", fontsize=12)
page1.insert_text(
    (50, 80),
    "ООО «Ромашка», именуемое в дальнейшем «Арендодатель», "
    "в лице директора Иванова И.И., с одной стороны, и "
    "ООО «Лютик», именуемое «Арендатор», в лице директора Петрова П.П., "
    "с другой стороны, заключили настоящий договор о нижеследующем:",
    fontsize=10,
)
page1.insert_text((50, 160), "1. ПРЕДМЕТ ДОГОВОРА", fontsize=10)
page1.insert_text(
    (50, 180),
    "1.1. Арендодатель передаёт Арендатору во временное пользование...",
    fontsize=10,
)

buf = io.BytesIO()
doc.save(buf)
doc2 = fitz.open(stream=buf.getvalue(), filetype="pdf")

page = doc2[0]

print("=== get_text('text') repr ===")
print(repr(page.get_text("text")))

print("\n=== splitlines ===")
for i, line in enumerate(page.get_text("text").splitlines()):
    print(f"  [{i}] {repr(line.strip())}")

print("\n=== get_text('dict') blocks -> lines -> spans ===")
d = page.get_text("dict")
for bi, block in enumerate(d.get("blocks", [])):
    if block.get("type") != 0:
        print(f"  block[{bi}] IMAGE")
        continue
    for li, line_obj in enumerate(block.get("lines", [])):
        spans_text = "".join(s.get("text", "") for s in line_obj.get("spans", []))
        print(f"  block[{bi}].line[{li}] = {repr(spans_text)}")

print("\n=== get_text('blocks') raw ===")
for bi, block in enumerate(page.get_text("blocks")):
    print(f"  [{bi}] type={block[6]} text={repr(block[4][:120])}")

doc2.close()
