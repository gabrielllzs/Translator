"""PDF vertalen met behoud van de originele layout.

Voor digitale PDF's (met echte tekst) wordt de tekst op de pagina zelf
vervangen: de originele tekst wordt verwijderd en de vertaling komt in
precies hetzelfde vak terug, met dezelfde grootte, kleur en vet/cursief.
Afbeeldingen, lijnen, tabellen, stempels en logo's worden niet aangeraakt.

Gescande pagina's (alleen een afbeelding, eventueel met onzichtbare
OCR-tekst) kunnen zo niet vertaald worden; die worden apart vertaald en
als extra pagina direct na de originele pagina ingevoegd, zodat er nooit
iets verloren gaat.
"""
import html
import io
import re
from pathlib import Path

import fitz

from docx_writer import (
    PAGE_MARKER, _BULLET_RE, _HEADING_RE, _INLINE_RE, _RULE_RE, _TABLE_SEPARATOR_RE,
    _split_row, _strip_code_fence,
)

_WINDOWS_FONTS = Path("C:/Windows/Fonts")
# Lettertypen van Windows, zodat de vertaling in hetzelfde lettertype staat als
# het origineel (normaal, vet, cursief, vet+cursief). Naam in de PDF -> bestanden.
_FONT_FILES = {
    "calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "times": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "tahoma": ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
    "verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "segoe": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "trebuchet": ("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf"),
    "courier": ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"),
    "comic": ("comic.ttf", "comicbd.ttf", "comici.ttf", "comicz.ttf"),
    "microsoft sans": ("micross.ttf", "micross.ttf", "micross.ttf", "micross.ttf"),
}
# Onbekende lettertypen: het dichtstbijzijnde alternatief.
_DEFAULT_SANS, _DEFAULT_SERIF = "arial", "times"

_SERIF_FONTS = ("times", "georgia", "garamond", "cambria", "book", "palatino", "minion", "serif")
_BULLETS = ("•", "▪", "◦", "●", "■", "–", "-", "·", "", "")
# "1. ", "a) ", "iv. ", maar ook "12.110859/2015" (nummer direct gevolgd door een cijfer)
_NUMBERING_RE = re.compile(r"^\(?(\d{1,3}|[a-zA-Z]|[ivxIVX]{1,4})[.)](\s|\d)")

# Een pagina telt als digitaal vanaf zoveel zichtbare tekens.
_MIN_VISIBLE_CHARS = 20

# Getallen/datums ("12-03-2020") in Arabische tekst: MuPDF zet die anders in de
# verkeerde volgorde. Een "isolate" (LRI ... PDI) houdt ze links-naar-rechts.
_NUMBER_RE = re.compile(r"\d[\d\-./:,]*\d")

_MARKER_RE = re.compile(r"<r(\d+)>(.*?)</r\1>", re.DOTALL)
_ANY_MARKER_RE = re.compile(r"</?r\d+>")


def _has_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def _looks_like_text(text: str) -> bool:
    """Echte tekst, geen OCR-ruis zoals "--- -- ---i--" of codes zonder woorden."""
    chars = [ch for ch in text if not ch.isspace()]
    letters = sum(ch.isalpha() for ch in chars)
    if not letters:
        return False
    if len(chars) <= 3:  # "A.", "B.", "of"
        return re.fullmatch(r"[^\W\d_][.)]?|[^\W\d_]{2,3}", "".join(chars)) is not None
    has_word = re.search(r"[^\W\d_]{2,}", text) is not None
    return has_word and letters / len(chars) >= 0.25


def _is_visible(span) -> bool:
    """Onzichtbare tekst (zoals de OCR-laag die Adobe over een scan legt) telt niet mee."""
    if span.get("alpha", 255) == 0:
        return False
    char_flags = span.get("char_flags")
    # 8 = gevuld, 16 = omlijnd; geen van beide = onzichtbaar (render mode 3)
    return char_flags is None or bool(char_flags & 24)


def classify_pages(doc) -> list[str]:
    """Per pagina: 'digital' (echte zichtbare tekst), 'scan' (afbeelding) of 'empty'."""
    kinds = []
    for page in doc:
        # (get_texttrace() is hier niet bruikbaar: die liet Python crashen op sommige PDF's.)
        visible = sum(
            len(span["text"].strip())
            for block in page.get_text("dict")["blocks"] if block["type"] == 0
            for line in block["lines"] for span in line["spans"] if _is_visible(span)
        )
        if visible >= _MIN_VISIBLE_CHARS:
            kinds.append("digital")
        elif page.get_images() or visible:
            kinds.append("scan")
        else:
            kinds.append("empty")
    return kinds


def _isolate_numbers(text: str) -> str:
    return _NUMBER_RE.sub(lambda m: "⁦" + m.group(0) + "⁩", text)


def _available_fonts() -> set[str]:
    return {name for name, files in _FONT_FILES.items()
            if all((_WINDOWS_FONTS / f).exists() for f in files)}


def _font_css(rtl: bool = False) -> tuple[str, "fitz.Archive | None"]:
    """@font-face-regels voor de Windows-lettertypen die op deze computer staan.

    Zonder die bestanden (geen Windows) valt MuPDF terug op zijn ingebouwde
    lettertypen; Arabisch komt dan uit Noto Naskh.
    """
    available = _available_fonts()
    if not available:
        return "", None
    rules = []
    for family in sorted(available):
        for file, (weight, style) in zip(_FONT_FILES[family], (("normal", "normal"), ("bold", "normal"),
                                                               ("normal", "italic"), ("bold", "italic"))):
            rules.append(f"@font-face {{font-family: tr-{family.replace(' ', '-')}; src: url({file}); "
                         f"font-weight: {weight}; font-style: {style};}}")
    return "\n".join(rules), fitz.Archive(str(_WINDOWS_FONTS))


def _css_family(piece, available: set[str]) -> str:
    """CSS-lettertype voor een stuk tekst: hetzelfde als in het origineel als het kan."""
    generic = "serif" if piece.serif else "sans-serif"
    family = piece.family if piece.family in available else (_DEFAULT_SERIF if piece.serif else _DEFAULT_SANS)
    if family not in available:
        return generic
    return f"tr-{family.replace(' ', '-')}, {generic}"


class _Piece:
    """Stuk tekst met één opmaak (vergelijkbaar met een 'run' in Word)."""

    def __init__(self, span):
        flags = span["flags"]
        font = span["font"].lower()
        self.text = span["text"]
        self.size = round(span["size"], 1)
        self.color = span["color"]
        self.bold = bool(flags & 16) or "bold" in font or "black" in font
        self.italic = bool(flags & 2) or "italic" in font or "oblique" in font
        # De serif-vlag in PDF's is onbetrouwbaar; kijk naar de lettertypenaam.
        self.serif = any(name in font for name in _SERIF_FONTS) and "sans" not in font
        self.superscript = bool(flags & 1)
        self.family = next((name for name in _FONT_FILES if name in font), None)

    def style_key(self):
        return (self.size, self.color, self.bold, self.italic, self.serif, self.superscript, self.family)


class PdfSegment:
    """Een regel of alinea tekst op een vaste plek op de pagina."""

    def __init__(self, page_number: int, rect, line_rects: list, pieces: list[_Piece], centered: bool,
                 justified: bool = False, line_height: float | None = None):
        self.page_number = page_number
        self.rect = rect
        self.line_rects = line_rects
        self.pieces = pieces
        self.centered = centered
        self.justified = justified
        self.line_height = line_height
        texts = [p.text for p in pieces]
        # Tekens zonder bekende betekenis (\ufffd, bijv. het logo-symbool in een
        # OCR-document) kunnen niet opnieuw getekend worden: zo'n stuk blijft origineel.
        joined = "".join(texts)
        self.has_symbol = "\ufffd" in joined or any(ord(ch) < 32 and ch not in "\t\n\r" for ch in joined)
        self.translatable = _looks_like_text(joined) and not self.has_symbol
        self.baseline = None
        self.column_right = None
        if len(pieces) == 1:
            self.source = texts[0].strip()
        else:
            self.source = "".join(f"<r{i}>{t}</r{i}>" for i, t in enumerate(texts, 1))
        self.translation = None

    def _pieces_from(self, translated: str) -> list[tuple[_Piece, str]] | None:
        if len(self.pieces) == 1:
            return [(self.pieces[0], _ANY_MARKER_RE.sub("", translated))]
        matches = list(_MARKER_RE.finditer(translated))
        ids = sorted(int(m.group(1)) for m in matches)
        if ids != list(range(1, len(self.pieces) + 1)):
            return None
        result = []
        for idx, match in enumerate(matches):
            text = match.group(2)
            nxt = matches[idx + 1].start() if idx + 1 < len(matches) else len(translated)
            gap = translated[match.end():nxt]
            text += gap if gap.strip() else (" " if gap else "")
            result.append((self.pieces[int(match.group(1)) - 1], text))
        if any(_ANY_MARKER_RE.search(t) for _, t in result):
            return None
        return result

    def to_html(self, rtl: bool, available_fonts: set[str]) -> tuple[str, bool]:
        """HTML voor de vertaalde tekst. Tweede waarde: False als de terugval nodig was."""
        text = self.translation if self.translation is not None else self.source
        pieces = self._pieces_from(text) if self.translatable else [(p, p.text) for p in self.pieces]
        exact = pieces is not None
        if pieces is None:
            pieces = [(self.pieces[0], _ANY_MARKER_RE.sub("", text))]

        if rtl:
            # Losse leestekens (". ") als eigen stuk komen in MuPDF aan de verkeerde
            # kant van een Arabische zin terecht; plak ze aan het vorige stuk.
            merged = []
            for piece, piece_text in pieces:
                if merged and piece_text.strip() and not any(ch.isalnum() for ch in piece_text) \
                        and not piece.superscript:
                    merged[-1] = (merged[-1][0], merged[-1][1] + piece_text)
                else:
                    merged.append((piece, piece_text))
            pieces = merged

        spans = []
        for piece, piece_text in pieces:
            family = _css_family(piece, available_fonts)
            style = [
                f"font-family: {family}",
                f"font-size: {piece.size}pt",
                f"color: #{piece.color:06x}",
                f"font-weight: {'bold' if piece.bold else 'normal'}",
                f"font-style: {'italic' if piece.italic else 'normal'}",
            ]
            if piece.superscript:
                style.append("vertical-align: super")
            escaped = html.escape(piece_text)
            if rtl:
                escaped = _isolate_numbers(escaped)
            spans.append(f'<span style="{"; ".join(style)}">{escaped}</span>')

        # Let op: geen dir="rtl" — MuPDF negeert dan text-align. De Arabische
        # woordvolgorde gaat ook zonder goed; rechts uitlijnen doen we zelf.
        if self.centered:
            align = "center"
        elif self.justified:
            align = "justify"
        else:
            align = "right" if rtl else "left"
        content = "".join(spans)
        if rtl:
            # Rechts-naar-links-markering aan begin en eind, zodat leestekens aan
            # het eind van een zin aan de linkerkant komen.
            content = f"‏{content}‏"
        line_height = f"; line-height: {self.line_height:.1f}pt" if self.line_height else ""
        return f'<p style="text-align: {align}; margin: 0{line_height}">{content}</p>', exact


def _merge_pieces(spans) -> list[_Piece]:
    pieces = []
    for span in spans:
        if not span["text"]:
            continue
        piece = _Piece(span)
        if pieces and pieces[-1].style_key() == piece.style_key():
            pieces[-1].text += piece.text
        else:
            pieces.append(piece)
    return pieces


def _starts_new_item(text: str) -> bool:
    text = text.lstrip()
    return text.startswith(_BULLETS) or bool(_NUMBERING_RE.match(text))


class _Line:
    """Eén regel tekst, met een vak dat past bij de letters zelf.

    De vakken die de PDF zelf opgeeft zijn bij OCR-lettertypen soms veel te hoog
    (ascender van 2x de lettergrootte); daarom rekenen we vanaf de basislijn.
    """

    def __init__(self, spans):
        self.spans = spans
        # Lettergrootte van het grootste stuk tekst (OCR geeft losse tekens soms een rare grootte).
        self.size = max(spans, key=lambda sp: len(sp["text"].strip()))["size"]
        self.baseline = spans[0]["origin"][1]
        ascender = min(max(s.get("ascender", 0.9) for s in spans), 1.05)
        descender = max(min(s.get("descender", -0.25) for s in spans), -0.35)
        if ascender <= 0.3:
            ascender = 0.9
        x0 = min(s["bbox"][0] for s in spans)
        x1 = max(s["bbox"][2] for s in spans)
        self.rect = fitz.Rect(x0, self.baseline - ascender * self.size, x1, self.baseline - descender * self.size)
        self.right_edge = x1  # rechterkant van de kolom; wordt later bepaald
        # Lettertype van het grootste deel van de regel (zonder subset-voor-/achtervoegsels).
        longest = max(spans, key=lambda sp: len(sp["text"].strip()))
        self.font = re.sub(r"^\*|^[A-Z]{6}\+|-\d+$", "", longest["font"]).split("-")[0].split(",")[0].lower()

    @property
    def text(self) -> str:
        return "".join(s["text"] for s in self.spans)


def _separators(page) -> list:
    """Verticale lijnen (tabel- en kaderranden): daar loopt een regel nooit overheen.

    Ook dunne afbeeldingen tellen mee (in gescande/OCR-documenten zijn randen vaak
    plaatjes), maar een grote achtergrondafbeelding niet.
    """
    rects = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                rects.append(fitz.Rect(item[1], item[2]).normalize())
            elif item[0] == "re":
                r = fitz.Rect(item[1])
                rects += [fitz.Rect(r.x0, r.y0, r.x0, r.y1), fitz.Rect(r.x1, r.y0, r.x1, r.y1)]
    for image in page.get_image_info():
        rects.append(fitz.Rect(image["bbox"]))
    return [r for r in rects if r.width <= 3 and r.height > 3]


def _blocked(left, right, separators) -> bool:
    for sep in separators:
        if sep.y1 <= max(left.rect.y0, right.rect.y0) or sep.y0 >= min(left.rect.y1, right.rect.y1):
            continue
        # Een afbeelding/lijn die (deels) tussen de twee regels zit.
        if sep.x0 < right.rect.x0 - 0.5 and sep.x1 > left.rect.x1 + 0.5:
            return True
    return False


def _join_rows(lines: list, separators) -> list:
    """Voeg stukken op dezelfde regel samen.

    OCR knipt uitgevulde regels vaak op in losse stukken (grote woordafstand);
    die horen bij elkaar. Niet samenvoegen over een tabelrand of afbeelding heen.
    """
    lines = sorted(lines, key=lambda l: (l.baseline, l.rect.x0))
    rows = []
    for line in lines:
        if rows and abs(line.baseline - rows[-1][0].baseline) <= 0.3 * line.size:
            rows[-1].append(line)
        else:
            rows.append([line])
    joined = []
    for row in rows:
        row.sort(key=lambda l: l.rect.x0)
        current = row[0]
        for line in row[1:]:
            gap = line.rect.x0 - current.rect.x1
            # Uitgevulde regels hebben soms grote woordafstanden; kolommen staan verder uit elkaar.
            if -1 <= gap <= 4 * max(line.size, current.size) and abs(line.size - current.size) <= 1.5 \
                    and not _blocked(current, line, separators):
                spans = list(current.spans)
                if not spans[-1]["text"].endswith(" ") and not line.spans[0]["text"].startswith(" "):
                    spans.append({**line.spans[0], "text": " "})
                current = _Line(spans + line.spans)
            else:
                joined.append(current)
                current = line
        joined.append(current)
    return joined


def _set_right_edges(lines: list, separators):
    """Bepaal per regel waar de kolom rechts ophoudt.

    Een kader- of tabelrand rechts van de regel; anders de langste regel in de
    buurt met dezelfde linkerkant (bij een adresblok is dat bijv. het e-mailadres).
    """
    for line in lines:
        nearby = [l.rect.x1 for l in lines
                  if abs(l.rect.x0 - line.rect.x0) <= 3 and abs(l.baseline - line.baseline) <= 12 * line.size]
        edge = max(nearby + [line.rect.x1])
        borders = [sep.x0 for sep in separators
                   if sep.width <= 3 and sep.x0 >= line.rect.x1 - 0.5
                   and sep.y0 < line.rect.y1 and sep.y1 > line.rect.y0]
        # Alleen een rand vlak naast de tekst telt (niet een streepje aan de paginarand).
        if borders and min(borders) - edge <= 3 * line.size:
            edge = max(edge, min(borders) - 2)
        line.right_edge = edge


def _continues(group: list, line: "_Line") -> bool:
    """Is deze regel het vervolg van dezelfde alinea (automatische terugloop)?"""
    last, first = group[-1], group[0]
    text = line.text.strip()
    if not text or _starts_new_item(text):
        return False
    # (OCR geeft soms per regel een iets andere lettergrootte.)
    if abs(line.size - last.size) > max(1, 0.25 * last.size) or line.font != last.font:
        return False
    pitch = line.baseline - last.baseline
    if pitch < 0.6 * last.size or pitch > 1.8 * last.size:
        return False
    # Zelfde linkerkant (de eerste regel mag inspringen).
    aligned = abs(line.rect.x0 - last.rect.x0) <= 3
    first_indent = len(group) == 1 and 0 < last.rect.x0 - line.rect.x0 <= 4 * last.size
    # Opsomming met hangende inspringing: "1. tekst" en de volgende regel onder "tekst".
    hanging = len(group) == 1 and _starts_new_item(first.text) and 0 < line.rect.x0 - first.rect.x0 <= 5 * last.size
    if not (aligned or first_indent or hanging):
        return False
    # Klassieke vuistregel: als het eerste woord nog op de vorige regel had gepast,
    # was het een bewuste nieuwe regel (adres, opsomming, ...).
    right = max([l.rect.x1 for l in group] + [line.rect.x1, last.right_edge])
    left = min(l.rect.x0 for l in group + [line])
    if last.rect.width < 0.75 * (right - left):
        return False
    first_word = text.split()[0]
    first_word_width = line.rect.width * len(first_word) / max(len(line.text), 1)
    return last.rect.x1 + 0.3 * line.size + first_word_width > right - 1


def extract_segments(page, page_number: int) -> list[PdfSegment]:
    """Haal de tekst van een digitale pagina op, per regel of doorlopende alinea."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            # Alleen gewone horizontale tekst; gedraaide tekst blijft zoals hij is.
            if tuple(round(v) for v in line["dir"]) != (1, 0):
                continue
            spans = [span for span in line["spans"] if _is_visible(span) and span["text"]]
            if spans and "".join(s["text"] for s in spans).strip():
                lines.append(_Line(spans))
    if not lines:
        return []

    separators = _separators(page)
    lines = _join_rows(lines, separators)
    _set_right_edges(lines, separators)
    left_margin = min(l.rect.x0 for l in lines)
    page_center = (page.rect.x0 + page.rect.x1) / 2

    groups = []
    for line in lines:  # gesorteerd van boven naar beneden
        for group in reversed(groups[-8:]):
            if _continues(group, line):
                group.append(line)
                break
        else:
            groups.append([line])

    segments = []
    for group in groups:
        rect = fitz.Rect(group[0].rect)
        for line in group[1:]:
            rect |= line.rect
        spans = []
        for n, line in enumerate(group):
            if n and spans and not spans[-1]["text"].endswith((" ", "-")):
                spans.append({**line.spans[0], "text": " "})
            spans.extend(line.spans)
        pieces = _merge_pieces(spans)
        if not pieces:
            continue
        centered = all(
            abs((l.rect.x0 + l.rect.x1) / 2 - page_center) < 6 and l.rect.x0 > left_margin + 20 for l in group
        )
        justified, line_height = False, None
        if len(group) > 1:
            # Uitgevuld: minstens twee volle regels die precies tot dezelfde rechterkant lopen.
            full = group[:-1]
            justified = not centered and len(full) >= 2 and \
                max(l.rect.x1 for l in full) - min(l.rect.x1 for l in full) <= 2
            # Regelafstand van het origineel overnemen.
            line_height = (group[-1].baseline - group[0].baseline) / (len(group) - 1)
        seg = PdfSegment(page_number, rect, [l.rect for l in group], pieces, centered, justified, line_height)
        seg.baseline = group[0].baseline
        seg.column_right = max(l.right_edge for l in group)
        segments.append(seg)
    return segments


def mark_letterhead(segments_by_page: dict, page_heights: dict):
    """Logo/briefhoofd niet vertalen: tekst bovenaan die op 3+ pagina's op dezelfde plek staat.

    Alleen op plek, niet op tekst: OCR leest een logo op elke pagina net anders
    ("openbaar", "penbaar", "pen baa r").
    """
    top = [(n, seg) for n, segments in segments_by_page.items() for seg in segments
           if seg.rect.y1 < 0.15 * page_heights[n]]
    for n, seg in top:
        pages = {m for m, other in top if _overlaps(seg.rect, other.rect, 0.3)}
        if len(pages) >= 3:
            seg.translatable = False

    # Tekst direct naast een logo-symbool (een teken dat niet als letter te lezen is)
    # hoort bij dat logo, bijv. "ministerie" onder het symbool + "penbaar".
    for n, segments in segments_by_page.items():
        # Alleen bovenaan de pagina (een handtekening onderaan is geen logo).
        logos = [seg for seg in segments if seg.has_symbol and seg.rect.y1 < 0.2 * page_heights[n]]
        for seg in segments:
            for logo in logos:
                close = abs(seg.pieces[0].size - logo.pieces[0].size) <= 1.5 and \
                    seg.rect.y0 <= logo.rect.y1 + logo.rect.height and seg.rect.y1 >= logo.rect.y0 - logo.rect.height \
                    and seg.rect.x0 <= logo.rect.x1 + 20 and seg.rect.x1 >= logo.rect.x0 - 20
                if close:
                    seg.translatable = False


def _obstacles(page, segments) -> list:
    """Alles waar vertaalde tekst niet overheen mag lopen: andere tekst, lijnen, afbeeldingen."""
    rects = [s.rect for s in segments]
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                rects.append(fitz.Rect(item[1], item[2]).normalize())
            elif item[0] == "re":
                r = fitz.Rect(item[1])
                # Randen van een kader/cel als losse lijnen; een gevuld vlak als achtergrond negeren.
                rects += [fitz.Rect(r.x0, r.y0, r.x1, r.y0), fitz.Rect(r.x0, r.y1, r.x1, r.y1),
                          fitz.Rect(r.x0, r.y0, r.x0, r.y1), fitz.Rect(r.x1, r.y0, r.x1, r.y1)]
            elif item[0] == "qu":
                rects.append(item[1].rect)
    page_area = abs(page.rect)
    for image in page.get_image_info():
        rect = fitz.Rect(image["bbox"])
        # Een achtergrondafbeelding over (bijna) de hele pagina is geen obstakel.
        if abs(rect) < 0.5 * page_area:
            rects.append(rect)
    return rects


def _available_rect(seg, obstacles, text_area, rtl: bool):
    """Hoe ver mag de vertaling uitlopen (naar rechts/links en naar beneden) zonder iets te raken?"""
    r = seg.rect
    x0, x1 = r.x0, max(r.x1, text_area.x1)
    if seg.centered or rtl:
        x0 = min(r.x0, text_area.x0)
    if rtl and not seg.centered:
        # Rechts uitgelijnd tegen de rechterkant van de eigen kolom (niet die van de
        # pagina), zodat bijv. een adresblok links op zijn plek blijft.
        x1 = max(r.x1, seg.column_right or r.x1)
    for ob in obstacles:
        if ob is r or ob.y1 <= r.y0 + 1 or ob.y0 >= r.y1 - 1:
            continue
        if ob.x0 >= r.x1 - 0.5:
            x1 = min(x1, ob.x0 - 2)
        elif ob.x1 <= r.x0 + 0.5:
            x0 = max(x0, ob.x1 + 2)
    # Nooit kleiner dan het originele vak (een obstakel kan er net overlappen).
    x0, x1 = min(x0, r.x0), max(x1, r.x1)
    if seg.centered:
        # Gecentreerd blijven: aan beide kanten evenveel ruimte.
        half = min(r.x0 - x0, x1 - r.x1)
        x0, x1 = r.x0 - half, r.x1 + half

    y1 = max(r.y1, text_area.y1)
    # Bij krappe regelafstand overlappen de vakken van onder elkaar staande regels
    # een beetje; alles wat onder het midden van de laatste regel begint, telt als "eronder".
    last_line = seg.line_rects[-1]
    below_from = (last_line.y0 + last_line.y1) / 2
    for ob in obstacles:
        if ob is r or ob.x1 <= x0 + 0.5 or ob.x0 >= x1 - 0.5:
            continue
        if ob.y0 >= below_from:
            y1 = min(y1, ob.y0 - 0.5)
    return fitz.Rect(x0 - 1, r.y0 - 1, x1 + 1, max(y1, r.y1 + 1))


def apply_page(page, segments: list, rtl: bool, css: str, archive, available: set[str]) -> int:
    """Vervang de tekst op één pagina. Geeft het aantal terugvallen terug."""
    if not segments:
        return 0
    obstacles = _obstacles(page, segments)
    text_area = fitz.Rect(segments[0].rect)
    for seg in segments[1:]:
        text_area |= seg.rect
    # Alleen tekst met letters wordt vervangen; cijfers, datums, losse tekens en
    # piepkleine/vreemd gevormde vakken blijven gewoon het origineel.
    todo = [seg for seg in segments
            if seg.translatable and seg.translation is not None
            and seg.rect.width >= 2 and seg.rect.height >= 2]
    # Tekst die blijft staan mag niet per ongeluk mee weggehaald worden: overlapt een
    # te vertalen regel duidelijk met zo'n stuk (bijv. een logo), dan blijft die regel origineel.
    kept_lines = [line for seg in segments if seg not in todo for line in seg.line_rects]
    todo = [seg for seg in todo if not any(_overlaps(line, kept) for line in seg.line_rects for kept in kept_lines)]
    if not todo:
        return 0
    targets = [_available_rect(seg, obstacles, text_area, rtl) for seg in todo]

    for seg in todo:
        for line_rect in seg.line_rects:
            # Iets smaller in de hoogte, zodat tekst van de regel erboven/eronder blijft staan.
            inset = line_rect.height * 0.2
            page.add_redact_annot(fitz.Rect(line_rect.x0, line_rect.y0 + inset,
                                            line_rect.x1, line_rect.y1 - inset), fill=False)
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )
    fallbacks = 0
    for seg, rect in zip(todo, targets):
        body, exact = seg.to_html(rtl, available)
        if not exact:
            fallbacks += 1
        if seg.baseline is not None:
            offset = _baseline_offset(seg, rtl, available, css, archive)
            if offset is not None:
                rect = fitz.Rect(rect.x0, seg.baseline - offset, rect.x1, max(rect.y1, seg.baseline + seg.rect.height))
        _insert_text(page, rect, seg.rect, body, css, archive)
    return fallbacks


_OFFSET_CACHE = {}


def _baseline_offset(seg, rtl: bool, available: set[str], css: str, archive) -> float | None:
    """Afstand van de bovenkant van het tekstvak tot de eerste basislijn.

    Hangt af van lettertype, grootte en regelafstand; we meten het één keer op
    een kladpagina, zodat de vertaalde regel precies op de originele basislijn staat.
    """
    piece = seg.pieces[0]
    key = (piece.style_key(), round(seg.line_height or 0, 1), rtl, bool(archive))
    if key not in _OFFSET_CACHE:
        probe = PdfSegment(0, seg.rect, seg.line_rects, [piece], False, False, seg.line_height)
        probe.translation = "Hg"
        probe.translatable = True
        body, _ = probe.to_html(rtl, available)
        offset = None
        try:
            scratch = fitz.open()
            page = scratch.new_page(width=1000, height=400)
            page.insert_htmlbox(fitz.Rect(10, 10, 990, 390), body, css=css, archive=archive, scale_low=1)
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    if line["spans"]:
                        offset = line["spans"][0]["origin"][1] - 10
                        break
                if offset is not None:
                    break
            scratch.close()
        except Exception:
            offset = None
        _OFFSET_CACHE[key] = offset
    return _OFFSET_CACHE[key]


def _overlaps(a, b, share: float = 0.15) -> bool:
    inter = fitz.Rect(a) & b
    if inter.is_empty:
        return False
    return abs(inter) > share * min(abs(a), abs(b))


def translate_document(doc, segments_by_page: dict, rtl: bool, progress=None):
    """Maak een nieuw document met de vertaalde pagina's.

    Elke pagina wordt los bewerkt en meteen compact gemaakt: de lettertypen
    worden per tekstvak ingesloten, en zonder dit opruimen zou een document
    van honderden pagina's gigabytes aan geheugen kosten.
    Geeft (nieuw document, aantal terugvallen) terug.
    """
    css, archive = _font_css(rtl)
    available = _available_fonts() if archive is not None else set()
    out = fitz.open()
    fallbacks = 0
    for n in range(len(doc)):
        segments = segments_by_page.get(n)
        if not segments:
            out.insert_pdf(doc, from_page=n, to_page=n)
            continue
        single = fitz.open()
        single.insert_pdf(doc, from_page=n, to_page=n)
        fallbacks += apply_page(single[0], segments, rtl, css, archive, available)
        _fix_text_extraction(single)
        try:
            single.subset_fonts()
        except Exception:
            pass
        compact = fitz.open("pdf", single.tobytes(garbage=4, deflate=True))
        out.insert_pdf(compact)
        single.close()
        compact.close()
        if progress:
            progress(n + 1, len(doc))
    try:
        out.set_metadata(doc.metadata)
        out.set_toc(doc.get_toc(simple=False))
    except Exception:
        pass
    return out, fallbacks


_CMAP_SOFT_HYPHEN = re.compile(rb"(?m)^(\s*<[0-9A-Fa-f]+>\s+)<00[Aa][Dd]>(\s*)$")
_CMAP_NBSP = re.compile(rb"(?m)^(\s*<[0-9A-Fa-f]+>\s+)<00[Aa]0>(\s*)$")


def _fix_text_extraction(doc):
    """Zorg dat kopiëren/zoeken in de vertaalde tekst gewone spaties en streepjes geeft.

    Windows-lettertypen gebruiken hetzelfde teken voor spatie/harde spatie en
    streepje/afbreekstreepje; MuPDF koos de verkeerde (U+00A0 en U+00AD), waardoor
    bijv. een gekopieerde datum "12-03-2020" als "12032020" in Word verscheen.
    """
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Type")[1] != "/Font":
                continue
            kind, value = doc.xref_get_key(xref, "ToUnicode")
            if kind != "xref":
                continue
            cmap_xref = int(value.split()[0])
            data = doc.xref_stream(cmap_xref)
            # Alleen de doelwaarde van een losse koppeling ("<glyph> <00AD>") aanpassen.
            fixed = _CMAP_SOFT_HYPHEN.sub(rb"\1<002D>\2", data)
            fixed = _CMAP_NBSP.sub(rb"\1<0020>\2", fixed)
            if fixed != data:
                doc.update_stream(cmap_xref, fixed)
        except Exception:
            continue


def _insert_text(page, rect, original_rect, body: str, css: str, archive):
    """Zet de vertaling neer; bij problemen in het originele vak, desnoods kleiner."""
    for target in (rect, fitz.Rect(original_rect)):
        try:
            spare, _scale = page.insert_htmlbox(target, body, css=css, archive=archive, scale_low=0.05)
            if spare >= 0:
                return
        except Exception:
            continue
    # Laatste redmiddel: zonder ondergrens verkleinen, zodat de tekst er in elk geval staat.
    try:
        page.insert_htmlbox(rect, body, css=css, archive=archive, scale_low=0)
    except Exception:
        pass


def _inline_html(text: str, rtl: bool = False) -> str:
    out, pos = [], 0
    for match in _INLINE_RE.finditer(text):
        out.append(html.escape(text[pos:match.start()]))
        both, bold, italic = match.groups()
        inner = html.escape(both or bold or italic)
        if both:
            inner = f"<b><i>{inner}</i></b>"
        elif bold:
            inner = f"<b>{inner}</b>"
        else:
            inner = f"<i>{inner}</i>"
        out.append(inner)
        pos = match.end()
    out.append(html.escape(text[pos:]))
    result = "<br>".join("".join(out).split("&lt;br&gt;"))
    return f"‏{_isolate_numbers(result)}‏" if rtl else result


def markdown_to_html(markdown: str, rtl: bool = False) -> str:
    """Zet de Markdown-vertaling van een gescande pagina om naar eenvoudige HTML."""
    lines = _strip_code_fence(markdown).splitlines()
    parts, bullets = [], []

    def flush_bullets():
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line == PAGE_MARKER or _RULE_RE.match(line):
            flush_bullets()
            i += 1
            continue
        if line.startswith("|"):
            flush_bullets()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = _split_row(lines[i])
                if not all(_TABLE_SEPARATOR_RE.match(c) for c in row if c):
                    rows.append(row)
                i += 1
            parts.append("<table>" + "".join(
                "<tr>" + "".join(f"<td>{_inline_html(c, rtl)}</td>" for c in row) + "</tr>" for row in rows
            ) + "</table>")
            continue
        heading = _HEADING_RE.match(line)
        bullet = _BULLET_RE.match(line)
        if bullet:
            bullets.append(_inline_html(bullet.group(1), rtl))
        else:
            flush_bullets()
            if heading:
                level = min(len(heading.group(1)), 3)
                parts.append(f"<h{level}>{_inline_html(heading.group(2), rtl)}</h{level}>")
            else:
                parts.append(f"<p>{_inline_html(line, rtl)}</p>")
        i += 1
    flush_bullets()
    return "\n".join(parts)


def append_translation_page(doc, after_page: int, html_body: str, rtl: bool) -> int:
    """Voeg na een gescande pagina één of meer pagina's met de vertaling in.

    Geeft het aantal ingevoegde pagina's terug.
    """
    css, archive = _font_css(rtl)
    family = "tr-arial, sans-serif" if "arial" in _available_fonts() else "sans-serif"
    align = "right" if rtl else "left"
    css += f"\n* {{font-family: {family}; font-size: 10.5pt;}} p, li, td, h2, h3 {{text-align: {align};}} " \
           f"table {{border-collapse: collapse;}} td, th {{border: 1px solid #888; padding: 2px 4px;}} " \
           f"h1 {{font-size: 15pt; text-align: center;}} h2 {{font-size: 12.5pt;}} h3 {{font-size: 11pt;}}"
    story = fitz.Story(f"<body>{html_body}</body>", user_css=css, archive=archive)
    page_rect = doc[after_page].rect
    where = page_rect + (50, 50, -50, -50)

    stream = io.BytesIO()
    writer = fitz.DocumentWriter(stream)
    more = True
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    buffer = fitz.open("pdf", stream.getvalue())
    doc.insert_pdf(buffer, start_at=after_page + 1)
    return len(buffer)
