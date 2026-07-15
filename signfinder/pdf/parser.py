"""Парсинг PDF и DOCX в структурированный вид."""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fitz
except ImportError:
    fitz = None  # type: ignore[assignment]


@dataclass
class Word:
    text: str
    bbox: tuple  # (x0, y0, x1, y1) в пунктах


@dataclass
class ParsedPage:
    page_num: int  # 0-indexed
    text: str
    words: list = field(default_factory=list)
    layout: str = "single_column"          # "single_column" | "dual_column_vertical"
    gutter_x: float | None = None          # x-координата коридора (pt)
    languages: list = field(default_factory=list)  # ["en"] или ["mk", "en"]
    dual_zones: list = field(default_factory=list)  # [(y0, y1, gutter_x), ...] — локальные двухколоночные строки (напр. футер "Заказчик___  Подрядчик___") даже когда layout страницы single_column


@dataclass
class ParsedDocument:
    filename: str
    language: str                          # первичный язык (обратная совместимость)
    languages: list = field(default_factory=list)  # все языки документа
    layout: str = "single_column"
    gutter_x: float | None = None
    pages: list = field(default_factory=list)
    pdf_bytes: bytes = b""
    _langdetect_calls: int = 0             # служебное поле для профилирования (v1.19)


def _detect_gutter(words_raw: list, page_width: float) -> float | None:
    """Обнаружить вертикальный коридор между колонками.

    words_raw — список кортежей из fitz get_text("words"): (x0,y0,x1,y1,text,...)
    Ищет полосу x в центральной зоне (35-65% ширины), через которую проходит ≤2 слова.
    Возвращает x-координату коридора или None если одна колонка.

    ПРОВЕРЕНО на 6 документах клиента: безошибочно делит 4 двуязычных (0-1 пересечение)
    от 1 одноколоночного (10 пересечений).
    """
    if not words_raw:
        return None
    lo, hi = int(page_width * 0.35), int(page_width * 0.65)
    best_cut, best_cross = None, len(words_raw) + 1
    for cut in range(lo, hi, 5):
        crossing = sum(1 for w in words_raw if w[0] < cut < w[2])
        if crossing < best_cross:
            best_cross, best_cut = crossing, cut
    return float(best_cut) if (best_cross <= 2 and best_cut is not None) else None


def _detect_local_dual_zones(words_raw: list, page_width: float) -> list:
    """Найти узкие Y-полосы с локальным двухколоночным разрывом на однокол­оночной
    странице (напр. строка футера "Заказчик____   Подрядчик____" или блок
    реквизитов с двумя сторонами рядом).

    _detect_gutter смотрит на ВСЕ слова страницы разом и ищет один разрез с
    ≤2 пересечений — на странице, где абзацы основного текста идут через всю
    ширину, любой разрез в зоне 35-65% пересекают десятки слов абзацев, и
    локальный разрыв в футере/реквизитах тонет в этом шуме (доказано на
    signed_1ДоговорЛебедев.pdf: гутter не находится ни на одной странице,
    хотя футер каждой страницы физически состоит из двух блоков через пробел
    ~150pt).

    Здесь вместо одного разреза по всей странице — слова сначала группируются
    в строки по вертикальному перекрытию bbox (это же покрывает случай когда
    подчёркивание и подпись-лейбл — формально разные MuPDF "line", но
    физически одна визуальная строка, как в футерах LibreOffice-конвертации),
    а затем в КАЖДОЙ строке независимо ищется наибольший горизонтальный
    разрыв. Строка считается локальной двухколоночной зоной если разрыв
    ≥8% ширины страницы и его центр лежит в 25-75% ширины.

    Возвращает список (y0, y1, gutter_x) только для таких строк — это НЕ
    layout всей страницы, а точечные протоколы для _filter_by_our_side_context.
    """
    if not words_raw:
        return []

    rows: list = []
    for w in sorted(words_raw, key=lambda w: w[1]):
        for row in rows:
            row_y0 = min(r[1] for r in row)
            row_y1 = max(r[3] for r in row)
            if w[1] < row_y1 and w[3] > row_y0:  # вертикальное перекрытие bbox
                row.append(w)
                break
        else:
            rows.append([w])

    zones = []
    for row in rows:
        if len(row) < 2:
            continue
        row_sorted = sorted(row, key=lambda r: r[0])
        best_gap, best_center = 0.0, None
        for a, b in zip(row_sorted, row_sorted[1:]):
            gap = b[0] - a[2]
            if gap > best_gap:
                best_gap, best_center = gap, (a[2] + b[0]) / 2
        if (best_center is not None
                and best_gap >= page_width * 0.08
                and page_width * 0.25 <= best_center <= page_width * 0.75):
            row_y0 = min(r[1] for r in row)
            row_y1 = max(r[3] for r in row)
            zones.append((row_y0, row_y1, best_center))

    return zones


def _build_column_text(words_raw: list, x_max: float | None = None,
                       x_min: float | None = None) -> str:
    """Собрать текст из слов в горизонтальном диапазоне, сортируя по (top, x0)."""
    if x_max is not None:
        ws = [w for w in words_raw if w[2] <= x_max]
    elif x_min is not None:
        ws = [w for w in words_raw if w[0] >= x_min]
    else:
        ws = words_raw
    ws_sorted = sorted(ws, key=lambda w: (round(w[1]), w[0]))
    lines, cur_line, cur_y = [], [], None
    for w in ws_sorted:
        wy = round(w[1])
        if cur_y is None or abs(wy - cur_y) > 3:
            if cur_line:
                lines.append(" ".join(c[4] for c in cur_line))
            cur_line, cur_y = [w], wy
        else:
            cur_line.append(w)
    if cur_line:
        lines.append(" ".join(c[4] for c in cur_line))
    return "\n".join(lines)


def docx_to_pdf(docx_bytes: bytes) -> bytes:
    """Конвертация DOCX в PDF через LibreOffice headless."""
    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = Path(tmpdir) / "input.docx"
        docx_path.write_bytes(docx_bytes)
        subprocess.run(
            [
                "soffice", "--headless", "--convert-to", "pdf",
                "--outdir", tmpdir, str(docx_path),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        pdf_path = Path(tmpdir) / "input.pdf"
        if not pdf_path.exists():
            raise RuntimeError("LibreOffice не создал PDF из DOCX")
        return pdf_path.read_bytes()


def parse_pdf_bytes(pdf_bytes: bytes, filename: str) -> ParsedDocument:
    """Парсинг PDF — текст и слова с координатами. Детектирует двухколоночный layout."""
    from langdetect import detect as _detect_raw

    langdetect_calls = [0]

    def _detect(text: str) -> str:
        langdetect_calls[0] += 1
        return _detect_raw(text)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    full_text_parts = []
    cached_col_langs: tuple[str, str] | None = None  # кэш языков колонок по первой dual-стр.

    for page_num, page in enumerate(doc):
        words_raw = page.get_text("words")
        pw = page.rect.width

        gutter = _detect_gutter(words_raw, pw)

        if gutter:
            left_text = _build_column_text(words_raw, x_max=gutter)
            right_text = _build_column_text(words_raw, x_min=gutter)
            page_text = left_text + "\n---\n" + right_text

            if cached_col_langs is None:
                # Первая dual-страница — детектируем и кэшируем
                try:
                    lang_left = _detect(left_text[:500]) if left_text.strip() else "unknown"
                except Exception:
                    lang_left = "unknown"
                try:
                    lang_right = _detect(right_text[:500]) if right_text.strip() else "unknown"
                except Exception:
                    lang_right = "unknown"
                cached_col_langs = (lang_left, lang_right)
            else:
                lang_left, lang_right = cached_col_langs

            page_langs = list(dict.fromkeys([lang_left, lang_right]))
            p_layout = "dual_column_vertical"
        else:
            page_text = page.get_text()
            page_langs = []
            p_layout = "single_column"
            gutter = None

        words = [Word(text=w[4], bbox=(w[0], w[1], w[2], w[3])) for w in words_raw]
        full_text_parts.append(page_text)
        dual_zones = _detect_local_dual_zones(words_raw, pw)
        pages.append(ParsedPage(
            page_num=page_num, text=page_text, words=words,
            layout=p_layout, gutter_x=gutter, languages=page_langs,
            dual_zones=dual_zones,
        ))

    doc.close()

    full_text = "\n".join(full_text_parts)[:5000]
    try:
        language = _detect(full_text) if full_text.strip() else "unknown"
    except Exception:
        language = "unknown"

    all_langs: list = []
    for p in pages:
        for lg in p.languages:
            if lg not in all_langs and lg != "unknown":
                all_langs.append(lg)
    if not all_langs:
        all_langs = [language]

    # dual только если >=50% страниц dual. Защита от single-документов где
    # на одной странице два блока подписи бок о бок (IndividualProject стр.3).
    dual_pages = sum(1 for p in pages if p.layout == "dual_column_vertical")
    doc_layout = "dual_column_vertical" if (pages and dual_pages >= len(pages) / 2) else "single_column"
    doc_gutter = next((p.gutter_x for p in pages if p.gutter_x is not None), None)

    return ParsedDocument(
        filename=filename,
        language=language,
        languages=all_langs,
        layout=doc_layout,
        gutter_x=doc_gutter,
        pages=pages,
        pdf_bytes=pdf_bytes,
        _langdetect_calls=langdetect_calls[0],
    )


def parse_document(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Универсальный парсер — PDF или DOCX по расширению."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return parse_pdf_bytes(file_bytes, filename)
    elif ext == ".docx":
        pdf_bytes = docx_to_pdf(file_bytes)
        return parse_pdf_bytes(pdf_bytes, filename)
    else:
        raise ValueError(f"Неподдерживаемый формат: {ext}")
