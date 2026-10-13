"""Tekst in een Word-document (.docx) vervangen zonder de layout aan te raken.

Het document wordt niet opnieuw opgebouwd: we openen het origineel, vervangen
alleen de tekst in de bestaande runs en slaan het op. Pagina-instellingen,
stijlen, tabellen, afbeeldingen, kop-/voetteksten, nummering enz. blijven zo
precies zoals ze waren.
"""
import re
from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.opc.part import XmlPart
from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


W_P, W_R, W_T, W_RPR, W_PPR = _w("p"), _w("r"), _w("t"), _w("rPr"), _w("pPr")

# Delen van het pakket waarin vertaalbare tekst staat.
_TEXT_PART_SUFFIXES = (".main+xml", ".header+xml", ".footer+xml", ".footnotes+xml", ".endnotes+xml")

# Volgorde van elementen binnen w:pPr en w:rPr volgens het OOXML-schema.
# Word weigert (of "repareert") bestanden waarin deze volgorde niet klopt.
_PPR_ORDER = [_w(t) for t in (
    "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
    "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
    "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
    "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc",
    "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr",
    "sectPr", "pPrChange",
)]
_RPR_ORDER = [_w(t) for t in (
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
    "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
    "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
    "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
)]

_MARKER_RE = re.compile(r"<r(\d+)>(.*?)</r\1>", re.DOTALL)
_ANY_MARKER_RE = re.compile(r"</?r\d+>")


def _has_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def _insert_in_order(parent, child, order: list[str]):
    """Voeg child in op de plek die het schema voorschrijft."""
    idx = order.index(child.tag)
    later = set(order[idx + 1:])
    for i, existing in enumerate(parent):
        if existing.tag in later:
            parent.insert(i, child)
            return child
    parent.append(child)
    return child


def _get_or_add_first(parent, tag):
    """Haal pPr/rPr op, of maak het aan als eerste kind (zoals het schema vereist)."""
    el = parent.find(tag)
    if el is None:
        el = etree.SubElement(parent, tag)
        parent.remove(el)
        parent.insert(0, el)
    return el


def _set_flag(props, tag, order, on: bool):
    """Zet een aan/uit-eigenschap (zoals w:bidi of w:rtl) expliciet aan of uit."""
    el = props.find(tag)
    if el is None:
        el = _insert_in_order(props, etree.Element(tag), order)
    if on:
        el.attrib.pop(_w("val"), None)
    else:
        el.set(_w("val"), "0")


def _run_text(run) -> str:
    return "".join(t.text or "" for t in run.findall(W_T))


def _is_pure_text_run(run) -> bool:
    return all(child.tag in (W_RPR, W_T) for child in run)


def _own_runs(p) -> list:
    """Runs van deze alinea, zonder runs uit tekstvakken die erin genest zijn."""
    return [r for r in p.iter(W_R) if next(r.iterancestors(W_P), None) is p]


def _split_mixed_run(run):
    """Splits een run met tekst én tabs/regeleinden in losse runs.

    Zo blijft bijvoorbeeld "Naam:<tab>Jan" na vertaling netjes in twee
    delen met de tab ertussen, in plaats van dat de tab naar het eind schuift.
    """
    children = [c for c in run if c.tag != W_RPR]
    if not any(c.tag == W_T for c in children) or all(c.tag == W_T for c in children):
        return
    groups, current = [], []
    for child in children:
        is_text = child.tag == W_T
        if current and (is_text != (current[-1].tag == W_T) or not is_text):
            groups.append(current)
            current = []
        current.append(child)
    groups.append(current)

    rpr = run.find(W_RPR)
    parent = run.getparent()
    position = parent.index(run)
    for offset, group in enumerate(groups):
        new_run = etree.Element(W_R, attrib=dict(run.attrib))
        if rpr is not None:
            new_run.append(deepcopy(rpr))
        for child in group:
            new_run.append(child)
        parent.insert(position + offset, new_run)
    parent.remove(run)


def _merge_equal_runs(runs: list) -> list:
    """Voeg aangrenzende tekst-runs met exact dezelfde opmaak samen.

    Word knipt tekst vaak onnodig op (spellingscontrole, bewerkingshistorie).
    Samenvoegen voorkomt dat één zin in tien losse stukjes wordt vertaald.
    """
    merged = []
    for run in runs:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and prev.getnext() is run
            and _is_pure_text_run(prev)
            and _is_pure_text_run(run)
            and _rpr_key(prev) == _rpr_key(run)
        ):
            _set_run_text(prev, _run_text(prev) + _run_text(run))
            run.getparent().remove(run)
            continue
        merged.append(run)
    return merged


def _rpr_key(run) -> bytes:
    rpr = run.find(W_RPR)
    return b"" if rpr is None else etree.tostring(rpr)


def _set_run_text(run, text: str):
    texts = run.findall(W_T)
    texts[0].text = text
    texts[0].set(XML_SPACE, "preserve")
    for extra in texts[1:]:
        run.remove(extra)


class Segment:
    """Eén alinea met vertaalbare tekst."""

    def __init__(self, paragraph, runs: list):
        self.paragraph = paragraph
        # Runs met alleen spaties krijgen geen markering; ze blijven staan als scheiding.
        self.runs = [r for r in runs if _run_text(r).strip()]
        if len(self.runs) == 1:
            self.source = _run_text(self.runs[0])
        else:
            self.source = "".join(f"<r{i}>{_run_text(r)}</r{i}>" for i, r in enumerate(self.runs, 1))

    def apply(self, translated: str) -> bool:
        """Zet de vertaling terug in de runs. Geeft False als de terugval nodig was."""
        if len(self.runs) == 1:
            _set_run_text(self.runs[0], _ANY_MARKER_RE.sub("", translated))
            return True

        pieces = self._parse_markers(translated)
        if pieces is None:
            _set_run_text(self.runs[0], _ANY_MARKER_RE.sub("", translated))
            for run in self.runs[1:]:
                _set_run_text(run, "")
            return False

        for i, run in enumerate(self.runs, 1):
            _set_run_text(run, pieces[i])
        return True

    def _parse_markers(self, translated: str) -> dict | None:
        matches = list(_MARKER_RE.finditer(translated))
        ids = [int(m.group(1)) for m in matches]
        if sorted(ids) != list(range(1, len(self.runs) + 1)):
            return None

        pieces = {int(m.group(1)): m.group(2) for m in matches}
        # Tekst buiten de markeringen niet kwijtraken: plak hem aan het vorige stuk.
        for idx, match in enumerate(matches):
            end = match.end()
            next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(translated)
            gap = translated[end:next_start]
            if idx == 0 and translated[:match.start()].strip():
                key = int(match.group(1))
                pieces[key] = translated[:match.start()].strip() + " " + pieces[key]
            if not gap:
                continue
            key = int(match.group(1))
            if gap.strip():
                pieces[key] += gap
            elif idx + 1 < len(matches):
                nxt = int(matches[idx + 1].group(1))
                if not pieces[key][-1:].isspace() and not pieces[nxt][:1].isspace():
                    pieces[key] += " "

        if any(_ANY_MARKER_RE.search(text) for text in pieces.values()):
            return None
        return pieces

    def set_direction(self, rtl: bool):
        set_direction(self.paragraph, self.runs, rtl)


def set_direction(paragraph, runs: list, rtl: bool):
    """Leesrichting van een alinea en haar runs afstemmen op de doeltaal."""
    ppr = _get_or_add_first(paragraph, W_PPR)
    _set_flag(ppr, _w("bidi"), _PPR_ORDER, rtl)
    for run in runs:
        rpr = _get_or_add_first(run, W_RPR)
        _set_flag(rpr, _w("rtl"), _RPR_ORDER, rtl)
        if rtl:
            # Arabisch gebruikt de "complex script"-varianten van vet/cursief/grootte.
            for base, cs in (("b", "bCs"), ("i", "iCs"), ("sz", "szCs")):
                src = rpr.find(_w(base))
                if src is not None and rpr.find(_w(cs)) is None:
                    _insert_in_order(rpr, etree.Element(_w(cs), attrib=dict(src.attrib)), _RPR_ORDER)


class DocxLayout:
    """Een geopend .docx-bestand waarvan alleen de tekst vervangen wordt."""

    def __init__(self, path: Path):
        self.document = Document(str(path))
        self._raw_parts = []  # (part, root) voor delen die python-docx niet als XML kent
        self._roots = []
        for part in self.document.part.package.iter_parts():
            content_type = part.content_type
            if "wordprocessingml" not in content_type and "ms-word" not in content_type:
                continue
            if not content_type.endswith(_TEXT_PART_SUFFIXES):
                continue
            if isinstance(part, XmlPart):
                self._roots.append(part.element)
            else:
                root = etree.fromstring(part.blob)
                self._raw_parts.append((part, root))
                self._roots.append(root)

    def plain_text(self) -> str:
        """Alle tekst in leesvolgorde: koptekst, hoofdtekst (met tabellen en
        tekstvakken), voettekst en voetnoten.

        Tabelcellen worden gescheiden door een tab, rijen en alinea's door een nieuwe regel.
        """
        mc_fallback = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

        def paragraph_text(p) -> str:
            parts = []
            for run in _own_runs(p):
                for child in run:
                    if child.tag == W_T:
                        parts.append(child.text or "")
                    elif child.tag in (_w("tab"), _w("ptab")):
                        parts.append("\t")
                    elif child.tag in (_w("br"), _w("cr")):
                        parts.append("\n")
            return "".join(parts)

        def text_boxes(p) -> list[str]:
            # Tekstvakken staan in Word vaak twee keer (nieuw + "fallback" voor oude Word).
            lines = []
            for box in p.iter(_w("txbxContent")):
                if any(a.tag == mc_fallback for a in box.iterancestors()):
                    continue
                if next(box.iterancestors(_w("txbxContent")), None) is not None:
                    continue
                lines += block_text(box)
            return lines

        def block_text(element) -> list[str]:
            lines = []
            for child in element:
                if child.tag == W_P:
                    lines.append(paragraph_text(child))
                    lines += text_boxes(child)
                elif child.tag == _w("tbl"):
                    for row in child.findall(_w("tr")):
                        cells = [" ".join(line for line in block_text(tc) if line.strip())
                                 for tc in row.findall(_w("tc"))]
                        lines.append("\t".join(cells))
                elif child.tag == _w("sdt"):
                    content = child.find(_w("sdtContent"))
                    if content is not None:
                        lines += block_text(content)
            return lines

        seen, header, footer = set(), [], []
        for section in self.document.sections:
            for part, target in ((section.header, header), (section.footer, footer)):
                if part.is_linked_to_previous and seen:
                    continue
                for line in block_text(part._element):
                    if line.strip() and line not in seen:
                        seen.add(line)
                        target.append(line)
        body = block_text(self.document.element.body)

        notes = []
        for root in self._roots:
            if root.tag in (_w("footnotes"), _w("endnotes")):
                for note in root:
                    # Scheidingslijnen (type separator) overslaan.
                    if note.get(_w("type")) is None:
                        text = " ".join(line for line in block_text(note) if line.strip())
                        if text.strip():
                            notes.append(text.strip())

        lines = header + [""] + body + [""] + footer + [""] + notes
        text = "\n".join(lines).strip()
        return re.sub(r"\n{3,}", "\n\n", text)

    def segments(self) -> list[Segment]:
        segments = []
        for root in self._roots:
            for p in list(root.iter(W_P)):
                for proof in p.findall(_w("proofErr")):
                    p.remove(proof)
                for run in _own_runs(p):
                    _split_mixed_run(run)
                runs = _merge_equal_runs([r for r in _own_runs(p) if _run_text(r)])
                if not _has_letters("".join(_run_text(r) for r in runs)):
                    continue
                segments.append(Segment(p, runs))
        return segments

    def save(self, path: Path):
        for part, root in self._raw_parts:
            part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        self.document.save(str(path))
