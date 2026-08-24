"""Refresh the existing 20-slide deck without changing its established visual style."""
from copy import deepcopy
from pathlib import Path
import re

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "foundry-patterns.pptx"
PURPLE = RGBColor(0x86, 0x61, 0xC5)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)


def shape_texts(slide):
    return [shape.text for shape in slide.shapes if getattr(shape, "has_text_frame", False)]


def slide_text(slide):
    values = shape_texts(slide)
    for shape in slide.shapes:
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                values.extend(cell.text for cell in row.cells)
    return "\n".join(values)


def set_text(target, value: str):
    text_frame = target.text_frame
    source_paragraph = text_frame.paragraphs[0]
    source_run = source_paragraph.runs[0] if source_paragraph.runs else None
    run_properties = (
        deepcopy(source_run._r.get_or_add_rPr()) if source_run is not None else None
    )
    paragraph_properties = (
        deepcopy(source_paragraph._p.pPr)
        if source_paragraph._p.pPr is not None
        else None
    )

    text_frame.clear()
    for index, line in enumerate(value.split("\n")):
        paragraph = text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
        if index > 0 and paragraph_properties is not None:
            existing = paragraph._p.pPr
            if existing is not None:
                paragraph._p.remove(existing)
            paragraph._p.insert(0, deepcopy(paragraph_properties))
        run = paragraph.add_run()
        run.text = line
        if run_properties is not None:
            existing = run._r.get_or_add_rPr()
            existing.getparent().replace(existing, deepcopy(run_properties))


def set_font_size(target, points):
    for paragraph in target.text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(points)


def replace_text(slide, old_values, new_value):
    if isinstance(old_values, str):
        old_values = (old_values,)
    for shape in slide.shapes:
        if getattr(shape, "has_text_frame", False) and shape.text in old_values:
            set_text(shape, new_value)
            return shape
    raise ValueError(f"Could not find any of {old_values!r} on slide")


def pattern_slides(presentation):
    result = {}
    for slide in presentation.slides:
        for shape in slide.shapes:
            if (
                getattr(shape, "has_text_frame", False)
                and shape.top / 914400 < 1.5
                and re.fullmatch(r"\d{2}", shape.text)
            ):
                result[int(shape.text)] = slide
    return result


def group_slides(presentation):
    result = {}
    for slide in presentation.slides:
        for value in shape_texts(slide):
            match = re.fullmatch(r"Group (\d) of 4", value)
            if match:
                result[int(match.group(1))] = slide
    return result


def find_slide(presentation, needle):
    return next(slide for slide in presentation.slides if needle in slide_text(slide))


def clone_shape_slide(presentation, template):
    """Clone a shape-only pattern slide; the deck uses no image relationships here."""
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    for shape in list(slide.shapes):
        slide.shapes._spTree.remove(shape.element)
    for shape in template.shapes:
        slide.shapes._spTree.insert_element_before(
            deepcopy(shape.element),
            "p:extLst",
        )
    return slide


def configure_new_pattern_slide(
    slide,
    *,
    number,
    title,
    quote,
    demo,
    nodes,
    arrow_labels,
    benefits,
):
    shapes = list(slide.shapes)
    set_text(shapes[5], f"{number:02d}")
    set_text(shapes[6], "PATTERN")
    set_text(shapes[7], title)
    set_font_size(shapes[7], 27)
    set_text(shapes[8], quote)
    set_font_size(shapes[8], 16)
    set_text(shapes[11], demo)
    set_font_size(shapes[11], 13)

    for index, text in zip((13, 15, 17, 19), nodes):
        set_text(shapes[index], text)
        set_font_size(shapes[index], 14)
    for index, text in zip((21, 23), arrow_labels):
        set_text(shapes[index], text)
        set_font_size(shapes[index], 10.5)
        shapes[index].text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        shapes[index].text_frame.margin_left = 0
        shapes[index].text_frame.margin_right = 0
        shapes[index].text_frame.margin_top = 0
        shapes[index].text_frame.margin_bottom = 0
        shapes[index].text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    # The template's labels were wider than the inter-box gaps, so the next
    # node clipped their final characters. Keep the first label inside the
    # 1.05-inch horizontal gap and the second clear of the right-hand node.
    shapes[21].left = Inches(3.72)
    shapes[21].top = Inches(3.43)
    shapes[21].width = Inches(0.96)
    shapes[21].height = Inches(0.48)
    shapes[21].text_frame.paragraphs[0].line_spacing = 1.05
    shapes[23].left = Inches(7.95)
    shapes[23].top = Inches(3.32)
    shapes[23].width = Inches(1.55)
    shapes[23].height = Inches(0.34)
    for index, text in zip((28, 31, 34), benefits):
        set_text(shapes[index], text)
        set_font_size(shapes[index], 13)


def ensure_new_pattern_slides(presentation):
    patterns = pattern_slides(presentation)
    template = patterns[1]
    specifications = (
        {
            "number": 13,
            "title": "Human approval for consequential tool actions",
            "quote": "\u201cPause the exact tool call. Approval controls intent; authorization still controls access.\u201d",
            "demo": "Read runs immediately \u00b7 reject creates zero effects \u00b7 approve + replay creates exactly one.",
            "nodes": (
                "Prompt agent\nread + proposed action",
                "Foundry approval\nexact name + arguments",
                "Operator decision\nseparate identity",
                "Change-control MCP\none-time nonce",
            ),
            "arrow_labels": ("approval\nrequest", "approve / reject"),
            "benefits": (
                "Read-only tools continue without an approval interruption",
                "Rejected and stale decisions fail closed with zero side effects",
                "Nonce + decision + effect IDs correlate; replay stays exactly once",
            ),
        },
        {
            "number": 14,
            "title": "Model adaptation (fine-tuning & evaluation)",
            "quote": "\u201cFine-tune stable behavior, not changing knowledge \u2014 and prove the gain on untouched data.\u201d",
            "demo": "Base benchmark \u2192 reviewed SFT job \u2192 identical held-out eval \u2192 release gate + cleanup.",
            "nodes": (
                "Held-out test\nnever used for training",
                "Base model\nbenchmark first",
                "Foundry SFT job\nreviewed JSONL",
                "Developer eval tier\nsame test + cleanup",
            ),
            "arrow_labels": ("baseline\nmetrics", "stable behavior"),
            "benefits": (
                "RAG / Search / Foundry IQ remain the path for changing knowledge",
                "Schema, accuracy, adherence, tokens and latency \u2014 not training loss",
                "No measured gain or any configured regression means no promotion",
            ),
        },
        {
            "number": 15,
            "title": "Agent lifecycle & promotion (dev \u2192 test \u2192 prod)",
            "quote": "\u201cPromote immutable versions behind one endpoint; roll back the selector, not the state.\u201d",
            "demo": "Failing candidate blocked \u00b7 passing candidate pinned \u00b7 same URL \u00b7 rollback restores v1 + state.",
            "nodes": (
                "Release manifest\ncommit + aliases",
                "Dev \u2192 test\nsmoke + cloud eval",
                "Stable endpoint\nselector \u2192 candidate",
                "Rollback\nselector \u2192 prior",
            ),
            "arrow_labels": ("immutable\nversion", "eval evidence"),
            "benefits": (
                "Current agent object model \u2014 no new legacy Agent Application",
                "Stable endpoint URL does not change across promotion or rollback",
                "OIDC + complete release record; conversation store is not deleted",
            ),
        },
    )
    for specification in specifications:
        slide = patterns.get(specification["number"])
        if slide is None:
            slide = clone_shape_slide(presentation, template)
        configure_new_pattern_slide(slide, **specification)


def set_table_rows(shape, rows):
    table = shape.table
    xml_rows = list(table._tbl.tr_lst)
    while len(xml_rows) > len(rows):
        table._tbl.remove(xml_rows.pop())
    while len(xml_rows) < len(rows):
        clone = deepcopy(xml_rows[-1])
        table._tbl.append(clone)
        xml_rows.append(clone)

    for row_index, values in enumerate(rows):
        table.rows[row_index].height = Inches(0.46 if row_index else 0.42)
        for column_index, value in enumerate(values):
            set_text(table.cell(row_index, column_index), value)


def equalize_table_height(shape, inches):
    height = Inches(inches)
    row_height = int(height / len(shape.table.rows))
    for row in shape.table.rows:
        row.height = row_height
    shape.height = height


def merge_table_row(shape, row_index):
    table = shape.table
    first = table.cell(row_index, 0)
    if not first.is_merge_origin:
        first.merge(table.cell(row_index, 1))


def insert_table_row(shape, source_index, before_index):
    """Insert an unmerged row by cloning an existing data row."""
    table = shape.table
    source = list(table._tbl.tr_lst)[source_index]
    target = list(table._tbl.tr_lst)[before_index]
    target.addprevious(deepcopy(source))


def set_group_slide(slide, title, promise, pattern_list):
    texts = shape_texts(slide)
    old_title = next(
        value
        for value in texts
        if value
        in {
            "Control plane",
            "Agent factory",
            "Orchestration & interop",
            "Operate & optimise",
            "Platform foundation & governance",
            "Agent construction & knowledge",
            "Orchestration & interoperability",
            "Lifecycle, assurance & operations",
        }
    )
    replace_text(slide, old_title, title)
    candidates = [
        shape
        for shape in slide.shapes
        if getattr(shape, "has_text_frame", False)
        and 3.4 < shape.top / 914400 < 4.2
        and shape.text != title
    ]
    set_text(candidates[0], promise)
    list_shape = next(
        shape
        for shape in slide.shapes
        if getattr(shape, "has_text_frame", False) and 4.2 < shape.top / 914400 < 4.8
    )
    set_text(list_shape, pattern_list)
    set_font_size(list_shape, 13 if len(pattern_list) > 95 else 15)


def style_search_card(background, text_shape, live):
    if live:
        background.fill.solid()
        background.fill.fore_color.rgb = PURPLE
        background.line.color.rgb = PURPLE
        background.line.dash_style = MSO_LINE_DASH_STYLE.SOLID
        color = WHITE
    else:
        background.fill.solid()
        background.fill.fore_color.rgb = WHITE
        background.line.color.rgb = PURPLE
        background.line.dash_style = MSO_LINE_DASH_STYLE.DASH
        color = PURPLE
    for paragraph in text_shape.text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.color.rgb = color


def update_pattern_three(slide):
    replace_text(
        slide,
        (
            "Web IQ published as our own MCP API on APIM \u2014 the gateway authenticates "
            "the caller, holds the key and meters every tool call.",
            "Web IQ via APIM + Azure AI Search via a Foundry agent \u2014 both live, both cited.",
        ),
        "Web IQ via APIM + Azure AI Search via a Foundry agent \u2014 both live, both cited.",
    )
    replace_text(
        slide,
        (
            "Web IQ (MCP) \u2014 cited web context",
            "Web IQ \u2014 LIVE via APIM",
        ),
        "Web IQ \u2014 LIVE via APIM",
    )
    replace_text(
        slide,
        (
            "Foundry IQ \u2014 Azure AI Search",
            "Azure AI Search \u2014 LIVE enterprise index",
        ),
        "Azure AI Search \u2014 LIVE enterprise index",
    )
    replace_text(
        slide,
        (
            "Work IQ \u2014 M365 org context",
            "Foundry IQ \u2014 managed KB (NARRATED)",
        ),
        "Foundry IQ \u2014 managed KB (NARRATED)",
    )
    replace_text(
        slide,
        (
            "Fabric IQ \u2014 Business context",
            "Fabric IQ + Work IQ \u2014 NARRATED",
        ),
        "Fabric IQ + Work IQ \u2014 NARRATED",
    )
    replace_text(
        slide,
        (
            "Caller sends an Entra token\nAPIM holds the Web IQ key",
            "APIM subscription key\nupstream Web IQ key stays on gateway",
        ),
        "APIM subscription key\nupstream Web IQ key stays on gateway",
    )
    replace_text(slide, ("no API key", "Basic v2 exception"), "Basic v2 exception")
    replace_text(
        slide,
        (
            "Governed MCP grounding \u2014 one AI-Gateway endpoint, any agent or model",
            "Web IQ on APIM \u2014 authenticated, metered, upstream key retained",
        ),
        "Web IQ on APIM \u2014 authenticated, metered, upstream key retained",
    )
    replace_text(
        slide,
        (
            "The key stays on APIM; callers authenticate with Entra and get metered",
            "AzureAISearchTool \u2014 project connection + cited enterprise index",
        ),
        "AzureAISearchTool \u2014 project connection + cited enterprise index",
    )
    replace_text(
        slide,
        (
            "All four IQ layers \u2014 world, enterprise, org and business meaning",
            "Foundry IQ, Fabric IQ and Work IQ \u2014 broader narrated layers",
        ),
        "Foundry IQ, Fabric IQ and Work IQ \u2014 broader narrated layers",
    )

    cards = sorted(
        [
            shape
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False)
            and shape.text
            and shape.left / 914400 > 8
            and 2.5 < shape.top / 914400 < 5.2
        ],
        key=lambda shape: shape.top,
    )
    backgrounds = []
    for card in cards:
        background = next(
            shape
            for shape in slide.shapes
            if not getattr(shape, "text", "")
            and abs(shape.left - (card.left - Inches(0.05))) < Inches(0.02)
            and abs(shape.top - card.top) < Inches(0.02)
        )
        backgrounds.append(background)
    for index, (background, card) in enumerate(zip(backgrounds, cards)):
        style_search_card(background, card, live=index < 2)

    connectors = sorted(
        [
            shape
            for shape in slide.shapes
            if not getattr(shape, "text", "")
            and 7.0 < shape.left / 914400 < 7.2
            and 2.5 < shape.top / 914400 < 5.2
        ],
        key=lambda shape: shape.top,
    )
    for index, connector in enumerate(connectors):
        connector.line.color.rgb = PURPLE
        connector.line.dash_style = (
            MSO_LINE_DASH_STYLE.SOLID if index < 2 else MSO_LINE_DASH_STYLE.DASH
        )

    if not any("SOLID = LIVE" in value for value in shape_texts(slide)):
        legend = slide.shapes.add_textbox(
            Inches(8.05),
            Inches(2.34),
            Inches(4.35),
            Inches(0.22),
        )
        legend.text_frame.margin_left = 0
        legend.text_frame.margin_right = 0
        legend.text_frame.margin_top = 0
        legend.text_frame.margin_bottom = 0
        legend.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        set_text(legend, "SOLID = LIVE    \u00b7    DASHED = NARRATED")
        for paragraph in legend.text_frame.paragraphs:
            paragraph.alignment = PP_ALIGN.CENTER
            for run in paragraph.runs:
                run.font.name = "Aptos"
                run.font.size = Pt(8)
                run.font.bold = True
                run.font.color.rgb = PURPLE


def update_pattern_five(slide):
    replace_text(
        slide,
        (
            "Request fans out to two specialists concurrently; Compliance returns BLOCK, then aggregate.",
            "Concurrent specialists, published as one Foundry-managed Responses endpoint.",
        ),
        "Concurrent specialists, published as one Foundry-managed Responses endpoint.",
    )
    replace_text(
        slide,
        ("Orchestrator", "Foundry-hosted endpoint"),
        "Foundry-hosted endpoint",
    )
    replace_text(slide, ("Aggregate", "Fan-in result"), "Fan-in result")
    replace_text(
        slide,
        (
            "Open, code-first (SK / AutoGen lineage), model-portable",
            "Managed endpoint \u00b7 compute \u00b7 versions \u00b7 Entra Agent ID",
        ),
        "Managed endpoint \u00b7 compute \u00b7 versions \u00b7 Entra Agent ID",
    )


def update_content(presentation):
    patterns = pattern_slides(presentation)
    groups = group_slides(presentation)

    title_slide = presentation.slides[0]
    replace_text(
        title_slide,
        (
            "12 patterns   \u00b7   12 demos   \u00b7   for architects & principal engineers",
            "15 patterns   \u00b7   15 demos   \u00b7   for architects & principal engineers",
        ),
        "15 patterns   \u00b7   15 demos   \u00b7   for architects & principal engineers",
    )

    entry = find_slide(presentation, "gateway gives you MODEL ACCESS")
    replace_text(
        entry,
        (
            "The wedge: keep your gateway, keep your cloud \u2014 add the factory",
            "Enterprise entry point: keep your gateway, keep your cloud \u2014 add the factory",
            "Keep your gateway and cloud \u2014 add the agent factory",
        ),
        "Keep your gateway and cloud \u2014 add the agent factory",
    )

    run_show = find_slide(presentation, "Run-of-show")
    tables = [shape for shape in run_show.shapes if getattr(shape, "has_table", False)]
    if len(tables[0].table.rows) == 9:
        insert_table_row(tables[0], source_index=3, before_index=4)
    # The source right-hand table has a merged section row at index 4. Insert a
    # normal data row before it so Pattern 9 keeps two independent cells.
    if len(tables[1].table.rows) == 8:
        insert_table_row(tables[1], source_index=3, before_index=4)
    set_table_rows(
        tables[0],
        [
            ("Index", "Patterns"),
            ("PLATFORM FOUNDATION & GOVERNANCE", ""),
            ("1", "AI gateway & model access (APIM)"),
            ("8", "AI safety (Prompt Shields + Content Safety)"),
            ("13", "Human approval for consequential tool actions"),
            ("AGENT CONSTRUCTION & KNOWLEDGE", ""),
            ("2", "Foundry Agent Service (prompt and hosted agents)"),
            ("3", "Microsoft IQ \u2014 the intelligence layer"),
            ("12", "Centralized Toolboxes (one governed MCP endpoint)"),
            ("14", "Model adaptation (fine-tuning & evaluation)"),
            ("10", "Memory (short-term + long-term)"),
        ],
    )
    merge_table_row(tables[0], 1)
    merge_table_row(tables[0], 5)
    set_table_rows(
        tables[1],
        [
            ("Index", "Patterns"),
            ("ORCHESTRATION & INTEROPERABILITY", ""),
            ("4", "Agentic Loop (build skills, not agents)"),
            ("5", "Multi-agent orchestration (Agent Framework)"),
            ("9", "Cross-cloud interop (MCP / A2A)"),
            ("LIFECYCLE, ASSURANCE & OPERATIONS", ""),
            ("7", "Evaluation & release gate"),
            ("6", "Observability & tracing (OpenTelemetry)"),
            ("11", "Cost & latency (prompt cache + Model Router)"),
            ("15", "Agent lifecycle & promotion (dev \u2192 test \u2192 prod)"),
        ],
    )
    merge_table_row(tables[1], 1)
    merge_table_row(tables[1], 5)
    equalize_table_height(tables[0], 4.48)
    equalize_table_height(tables[1], 4.48)
    replace_text(
        run_show,
        (
            "Twelve patterns in four groups. One slide, one live demo each.",
            "Fifteen patterns in four groups. One slide, one live demo each.",
        ),
        "Fifteen patterns in four groups. One slide, one live demo each.",
    )

    set_group_slide(
        groups[1],
        "Platform foundation & governance",
        "Establish governed model access and safety controls",
        "01  AI gateway   \u00b7   08  AI safety   \u00b7   13  Human approval",
    )
    set_group_slide(
        groups[2],
        "Agent construction & knowledge",
        "Build agents with governed knowledge, tools and memory",
        "02  Agent Service  \u00b7  03  Microsoft IQ  \u00b7  12  Toolboxes  \u00b7  14  Adaptation  \u00b7  10  Memory",
    )
    set_group_slide(
        groups[3],
        "Orchestration & interoperability",
        "Compose skills, specialists and cross-cloud systems",
        "04  Agentic Loop   \u00b7   05  Multi-agent orchestration   \u00b7   09  Cross-cloud interop",
    )
    set_group_slide(
        groups[4],
        "Lifecycle, assurance & operations",
        "Evaluate, observe and optimize every release",
        "07  Evaluation   \u00b7   06  Observability   \u00b7   11  Cost   \u00b7   15  Lifecycle",
    )

    replace_text(
        patterns[1],
        ("Wedge \u2192 AI Hub Gateway / Citadel", "AI gateway & model access (APIM)"),
        "AI gateway & model access (APIM)",
    )
    replace_text(
        patterns[1],
        (
            "Ships as AI Hub Gateway / Citadel (Foundry + APIM)",
            "Enterprise APIM + Foundry composition \u2014 no rip-and-replace",
        ),
        "Enterprise APIM + Foundry composition \u2014 no rip-and-replace",
    )
    factory_caption = next(
        shape
        for shape in entry.shapes
        if getattr(shape, "has_text_frame", False)
        and shape.text.startswith("Foundry \u2014 the agent factory")
    )
    factory_caption.text_frame.paragraphs[-1].runs[0].font.size = Pt(12)
    factory_caption.text_frame.paragraphs[-1].runs[0].font.color.rgb = RGBColor(
        0x60, 0x5E, 0x5C
    )
    replace_text(
        patterns[8],
        (
            "Governance (Prompt Shields + Content Safety)",
            "AI safety (Prompt Shields + Content Safety)",
        ),
        "AI safety (Prompt Shields + Content Safety)",
    )
    replace_text(
        patterns[2],
        (
            "Agent Service (prompt and hosted agent)",
            "Foundry Agent Service (prompt and hosted agents)",
        ),
        "Foundry Agent Service (prompt and hosted agents)",
    )
    update_pattern_three(patterns[3])
    update_pattern_five(patterns[5])
    replace_text(
        patterns[7],
        ("Evaluation \u2192 optimization (CI gate)", "Evaluation & release gate"),
        "Evaluation & release gate",
    )
    replace_text(
        patterns[7],
        (
            "Score the golden set locally AND in Foundry; a wrong 'suitable' tanks "
            "groundedness and the CI gate blocks the PR.",
            "Foundry cloud scorecard + CI release gate; a wrong answer blocks the PR.",
            "Generate candidate answers, score in Foundry, fail closed; demo mode plants one regression.",
        ),
        "Generate candidate answers, score in Foundry, fail closed; demo mode plants one regression.",
    )
    replace_text(
        patterns[7],
        (
            "Golden set\n+ planted wrong row",
            "Golden set\nquestions + policy context",
        ),
        "Golden set\nquestions + policy context",
    )
    replace_text(
        patterns[7],
        ("Agent", "Candidate\nprompt + model"),
        "Candidate\nprompt + model",
    )
    replace_text(
        patterns[7],
        (
            "Evaluators\ngroundedness \u00b7 tool-accuracy",
            "Evaluators\ngroundedness \u00b7 relevance \u00b7 coherence",
        ),
        "Evaluators\ngroundedness \u00b7 relevance \u00b7 coherence",
    )
    replace_text(
        patterns[7],
        (
            "Scores locally + uploads to the Foundry Evaluations tab",
            "Candidate answers are generated before cloud evaluation",
        ),
        "Candidate answers are generated before cloud evaluation",
    )
    replace_text(
        patterns[7],
        (
            "CI gate on every PR \u2014 regressions can't merge",
            "Scoped CI gate \u2014 relevant regressions can't merge",
        ),
        "Scoped CI gate \u2014 relevant regressions can't merge",
    )
    replace_text(
        patterns[6],
        (
            "Run enable_tracing.py \u2014 agent 'rm-assistant-traced' + its trace in BOTH "
            "Foundry Tracing and App Insights.",
            "Metadata-only OTel trace in Foundry + App Insights; content requires explicit opt-in.",
        ),
        "Metadata-only OTel trace in Foundry + App Insights; content requires explicit opt-in.",
    )
    replace_text(
        patterns[6],
        (
            "OpenTelemetry spans\ntokens \u00b7 latency \u00b7 cost",
            "OpenTelemetry spans\nmetadata \u00b7 tokens \u00b7 latency",
            "OpenTelemetry spans\nmetadata only \u00b7 tokens \u00b7 latency",
        ),
        "OpenTelemetry spans\nmetadata only \u00b7 tokens \u00b7 latency",
    )
    replace_text(
        patterns[6],
        (
            "Token + cost + latency per span \u2014 observability & FinOps",
            "Token + latency telemetry \u00b7 rate-card cost by agent/version",
        ),
        "Token + latency telemetry \u00b7 rate-card cost by agent/version",
    )
    outcome_label = next(
        shape
        for shape in patterns[6].shapes
        if getattr(shape, "has_text_frame", False)
        and shape.text == "Foundry \u2014 Agents + Tracing"
    )
    if outcome_label.top / 914400 > 4.7:
        for shape in patterns[6].shapes:
            if 4.5 < shape.top / 914400 < 5.5:
                shape.top -= Inches(0.22)
    set_font_size(
        next(
            shape
            for shape in groups[4].shapes
            if getattr(shape, "has_text_frame", False)
            and shape.text == "Lifecycle, assurance & operations"
        ),
        39,
    )


def reorder_slides(presentation):
    patterns = pattern_slides(presentation)
    groups = group_slides(presentation)
    title = presentation.slides[0]
    entry = find_slide(presentation, "gateway gives you MODEL ACCESS")
    run_show = find_slide(presentation, "Run-of-show")
    close = find_slide(presentation, "and the close")
    order = [
        title,
        entry,
        run_show,
        groups[1],
        patterns[1],
        patterns[8],
        patterns[13],
        groups[2],
        patterns[2],
        patterns[3],
        patterns[12],
        patterns[14],
        patterns[10],
        groups[3],
        patterns[4],
        patterns[5],
        patterns[9],
        groups[4],
        patterns[7],
        patterns[6],
        patterns[11],
        patterns[15],
        close,
    ]
    slide_ids = [slide.slide_id for slide in order]
    id_to_element = {
        int(element.get("id")): element for element in presentation.slides._sldIdLst
    }
    for element in list(presentation.slides._sldIdLst):
        presentation.slides._sldIdLst.remove(element)
    for slide_id in slide_ids:
        presentation.slides._sldIdLst.append(id_to_element[slide_id])


def update_footers(presentation):
    for slide_number, slide in enumerate(presentation.slides, start=1):
        candidates = [
            shape
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False)
            and shape.left / 914400 > 11.5
            and shape.top / 914400 > 6.8
            and shape.text.strip().isdigit()
        ]
        for shape in candidates:
            set_text(shape, str(slide_number))


def main():
    presentation = Presentation(DECK)
    ensure_new_pattern_slides(presentation)
    update_content(presentation)
    reorder_slides(presentation)
    update_footers(presentation)
    presentation.save(DECK)
    print(f"Refreshed {DECK.name}: 23 slides, 15 canonical patterns, new group order.")


if __name__ == "__main__":
    main()
