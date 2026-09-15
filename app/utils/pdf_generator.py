"""
PDF Generator — Converts Markdown Evidence Reports into Professional PDF Documents
Uses ReportLab with custom styling, tables, callouts, and clean typographic hierarchy.
"""
import io
import re
import html
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Adds running footer with page numbers and DEVAA watermark."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        # Running header
        self.drawString(54, 750, "DEVAA Autonomous Engineering Platform — Evidence Dossier")
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        self.line(54, 744, 558, 744)

        # Running footer
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 36, page_text)
        self.drawString(54, 36, "Confidential · Internal Engineering Report · Generated Automatically")
        self.line(54, 46, 558, 46)

        self.restoreState()


def clean_xml_text(text: str) -> str:
    """Escapes raw characters for ReportLab XML paragraph engine."""
    if not text:
        return ""
    # Convert bold **text** to <b>text</b>
    t = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    # Convert italic *text* to <i>text</i>
    t = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', t)
    # Convert inline code `text` to font courier
    t = re.sub(r'`([^`]+)`', r'<font face="Courier" color="#0f172a" backcolor="#f1f5f9">\1</font>', t)
    # Escape any unescaped & that isn't part of an entity
    t = re.sub(r'&(?!(?:amp|lt|gt|quot|apos);)', '&amp;', t)
    return t


def generate_evidence_pdf(markdown_content: str, title: str = "DEVAA Evidence Report") -> bytes:
    """
    Parses an evidence markdown string and compiles it into a formatted PDF stream.
    Returns: bytes of the generated PDF.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=64,
        bottomMargin=54,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#1e1b4b'),
        spaceAfter=6,
    )

    h1_style = ParagraphStyle(
        'Header1',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#1e293b'),
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        'Header2',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=colors.HexColor('#334155'),
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor('#1e293b'),
        spaceAfter=4,
    )

    bullet_style = ParagraphStyle(
        'BulletStyle',
        parent=body_style,
        leftIndent=14,
        firstLineIndent=-10,
        spaceAfter=3,
    )

    callout_style = ParagraphStyle(
        'CalloutText',
        parent=body_style,
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor('#475569'),
    )

    code_block_style = ParagraphStyle(
        'CodeBlock',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#0f172a'),
    )

    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=body_style,
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#1e293b'),
    )

    table_head_style = ParagraphStyle(
        'TableHead',
        parent=table_cell_style,
        fontName='Helvetica-Bold',
        textColor=colors.HexColor('#0f172a'),
    )

    story_elements = []

    lines = markdown_content.splitlines()
    in_code_block = False
    code_lines = []
    in_table = False
    table_rows = []

    def flush_table():
        nonlocal in_table, table_rows
        if table_rows:
            col_count = max(len(r) for r in table_rows)
            # Normalize row lengths
            normalized = []
            for r in table_rows:
                row_cells = []
                is_header = (r == table_rows[0])
                for c in r:
                    style_to_use = table_head_style if is_header else table_cell_style
                    safe_txt = clean_xml_text(c)
                    row_cells.append(Paragraph(safe_txt, style_to_use))
                while len(row_cells) < col_count:
                    row_cells.append(Paragraph("", table_cell_style))
                normalized.append(row_cells)

            # Standard column widths totaling ~504pt (printable area)
            width_per_col = 504.0 / col_count
            t = Table(normalized, colWidths=[width_per_col] * col_count)
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#0f172a')),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('LEFTPADDING', (0, 0), (-1, -1), 5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
            ]))
            story_elements.append(Spacer(1, 4))
            story_elements.append(t)
            story_elements.append(Spacer(1, 6))
            table_rows = []
            in_table = False

    def flush_code_block():
        nonlocal in_code_block, code_lines
        if code_lines:
            raw_text = "\n".join(code_lines)
            safe_text = html.escape(raw_text).replace('\n', '<br/>').replace(' ', '&nbsp;')
            p = Paragraph(safe_text, code_block_style)
            # Wrap in a light gray table container for card appearance
            box = Table([[p]], colWidths=[504])
            box.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
                ('PADDING', (0, 0), (-1, -1), 6),
            ]))
            story_elements.append(Spacer(1, 4))
            story_elements.append(box)
            story_elements.append(Spacer(1, 6))
            code_lines = []
            in_code_block = False

    for line in lines:
        stripped = line.strip()

        # Code block fence toggle
        if stripped.startswith("```"):
            if in_code_block:
                flush_code_block()
            else:
                if in_table:
                    flush_table()
                in_code_block = True
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Table detection
        if stripped.startswith("|") and stripped.endswith("|"):
            # Check if this is a separator line like |---|---|
            if re.match(r'^\|[\s\-:|]+\|$', stripped):
                continue
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            table_rows.append(cells)
            in_table = True
            continue
        elif in_table:
            flush_table()

        if not stripped:
            continue

        # Headers
        if stripped.startswith("# "):
            h_text = clean_xml_text(stripped[2:].strip())
            story_elements.append(Paragraph(h_text, title_style))
            story_elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#6366f1'), spaceAfter=8))
        elif stripped.startswith("## "):
            h_text = clean_xml_text(stripped[3:].strip())
            story_elements.append(Paragraph(h_text, h1_style))
            story_elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0'), spaceAfter=4))
        elif stripped.startswith("### "):
            h_text = clean_xml_text(stripped[4:].strip())
            story_elements.append(Paragraph(h_text, h2_style))
        elif stripped.startswith("> "):
            # Blockquote
            callout_txt = clean_xml_text(stripped[2:].strip())
            p = Paragraph(f"<b>ℹ️ Note:</b> {callout_txt}", callout_style)
            box = Table([[p]], colWidths=[504])
            box.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f0fdf4')),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#86efac')),
                ('PADDING', (0, 0), (-1, -1), 6),
            ]))
            story_elements.append(Spacer(1, 2))
            story_elements.append(box)
            story_elements.append(Spacer(1, 4))
        elif stripped.startswith(("- [ ]", "- [x]", "- [X]")):
            # Checkbox bullet
            checked = "[x]" in stripped.lower()
            icon = "<b>[PASS]</b> " if checked else "<b>[ ]</b> "
            content = clean_xml_text(stripped[5:].strip())
            color_prefix = f'<font color="{("#15803d" if checked else "#64748b")}">{icon}</font>'
            story_elements.append(Paragraph(f"• {color_prefix}{content}", bullet_style))
        elif stripped.startswith(("- ", "* ")):
            bullet_txt = clean_xml_text(stripped[2:].strip())
            story_elements.append(Paragraph(f"• {bullet_txt}", bullet_style))
        else:
            p_text = clean_xml_text(stripped)
            story_elements.append(Paragraph(p_text, body_style))

    if in_code_block:
        flush_code_block()
    if in_table:
        flush_table()

    # Build the document
    doc.build(story_elements, canvasmaker=NumberedCanvas)
    pdf_data = buffer.getvalue()
    buffer.close()
    return pdf_data
