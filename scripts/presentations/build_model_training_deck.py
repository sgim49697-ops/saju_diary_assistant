# build_model_training_deck.py - K0·MIX2K·LoRA 실험을 4장 발표용 PDF로 렌더링한다.

from __future__ import annotations

import argparse
from pathlib import Path

from reportlab.lib.colors import Color, HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

PAGE_W = 960
PAGE_H = 540

BG = HexColor("#F8FAFD")
BG_ALT = HexColor("#F3F7FC")
PANEL = HexColor("#FFFFFF")
PANEL_ALT = HexColor("#EDF3FA")
LINE = HexColor("#CDD8E6")
TEXT = HexColor("#182235")
MUTED = HexColor("#5E6E82")
CYAN = HexColor("#007F78")
BLUE = HexColor("#1D65BC")
PURPLE = HexColor("#6E51C8")
ORANGE = HexColor("#B85F00")
GREEN = HexColor("#0F7A4A")
RED = HexColor("#C83B55")
YELLOW = HexColor("#8A6200")

FONT_REGULAR = "DeckRegular"
FONT_BOLD = "DeckBold"


def register_fonts() -> None:
    candidates = [
        (
            Path("/mnt/c/Windows/Fonts/malgun.ttf"),
            Path("/mnt/c/Windows/Fonts/malgunbd.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        ),
    ]
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(regular)))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
            return
    raise FileNotFoundError("발표자료 렌더링에 사용할 한글 글꼴을 찾지 못했습니다.")


def y_from_top(top: float, height: float = 0) -> float:
    return PAGE_H - top - height


def rect(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    width: float,
    height: float,
    fill: Color,
    *,
    radius: float = 12,
    stroke: Color | None = None,
    stroke_width: float = 1,
) -> None:
    pdf.setFillColor(fill)
    if stroke is None:
        pdf.setStrokeColor(fill)
    else:
        pdf.setStrokeColor(stroke)
    pdf.setLineWidth(stroke_width)
    pdf.roundRect(
        x,
        y_from_top(top, height),
        width,
        height,
        radius,
        fill=1,
        stroke=1 if stroke is not None else 0,
    )


def line(
    pdf: canvas.Canvas,
    x1: float,
    top1: float,
    x2: float,
    top2: float,
    color: Color = LINE,
    width: float = 1,
) -> None:
    pdf.setStrokeColor(color)
    pdf.setLineWidth(width)
    pdf.line(x1, y_from_top(top1), x2, y_from_top(top2))


def text(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    value: str,
    size: float,
    color: Color = TEXT,
    font: str = FONT_REGULAR,
) -> None:
    pdf.setFont(font, size)
    pdf.setFillColor(color)
    pdf.drawString(x, PAGE_H - top - size, value)


def right_text(
    pdf: canvas.Canvas,
    right: float,
    top: float,
    value: str,
    size: float,
    color: Color = TEXT,
    font: str = FONT_REGULAR,
) -> None:
    pdf.setFont(font, size)
    pdf.setFillColor(color)
    pdf.drawRightString(right, PAGE_H - top - size, value)


def wrap_line(value: str, font: str, size: float, max_width: float) -> list[str]:
    if not value:
        return [""]

    words = value.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if pdfmetrics.stringWidth(candidate, font, size) <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = ""

        if pdfmetrics.stringWidth(word, font, size) <= max_width:
            current = word
            continue

        chunk = ""
        for character in word:
            candidate = f"{chunk}{character}"
            if chunk and pdfmetrics.stringWidth(candidate, font, size) > max_width:
                lines.append(chunk)
                chunk = character
            else:
                chunk = candidate
        current = chunk

    if current:
        lines.append(current)
    return lines


def wrapped_text(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    value: str,
    width: float,
    size: float,
    *,
    color: Color = TEXT,
    font: str = FONT_REGULAR,
    leading: float | None = None,
    max_lines: int | None = None,
) -> float:
    actual_leading = leading or size * 1.45
    lines: list[str] = []
    for index, raw_line in enumerate(value.splitlines()):
        if index and not raw_line:
            lines.append("")
        else:
            lines.extend(wrap_line(raw_line, font, size, width))

    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1].rstrip()
        while last and pdfmetrics.stringWidth(f"{last}…", font, size) > width:
            last = last[:-1]
        lines[-1] = f"{last}…"

    for index, rendered_line in enumerate(lines):
        text(pdf, x, top + index * actual_leading, rendered_line, size, color, font)
    return len(lines) * actual_leading


def pill(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    value: str,
    *,
    fill: Color = PANEL_ALT,
    color: Color = TEXT,
    font: str = FONT_BOLD,
    size: float = 8,
    padding_x: float = 10,
    height: float = 22,
) -> float:
    width = pdfmetrics.stringWidth(value, font, size) + padding_x * 2
    rect(pdf, x, top, width, height, fill, radius=height / 2)
    text(pdf, x + padding_x, top + (height - size) / 2 - 1, value, size, color, font)
    return width


def arrow(pdf: canvas.Canvas, x1: float, x2: float, top: float, color: Color = CYAN) -> None:
    line(pdf, x1, top, x2 - 7, top, color, 1.8)
    pdf.setFillColor(color)
    pdf.setStrokeColor(color)
    y = y_from_top(top)
    path = pdf.beginPath()
    path.moveTo(x2, y)
    path.lineTo(x2 - 8, y + 4)
    path.lineTo(x2 - 8, y - 4)
    path.close()
    pdf.drawPath(path, fill=1, stroke=0)


def draw_background(pdf: canvas.Canvas, page: int) -> None:
    pdf.setFillColor(BG if page % 2 else BG_ALT)
    pdf.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    pdf.setFillColor(HexColor("#E4EEFB"))
    pdf.circle(PAGE_W - 18, PAGE_H + 8, 170, fill=1, stroke=0)
    pdf.setFillColor(HexColor("#EEE9FA"))
    pdf.circle(PAGE_W + 30, PAGE_H + 30, 105, fill=1, stroke=0)

    pdf.setFillColor(HexColor("#D5E2F0"))
    for row in range(4):
        for column in range(8):
            pdf.circle(54 + column * 14, 22 + row * 14, 1.25, fill=1, stroke=0)


def footer(pdf: canvas.Canvas, page: int, section: str) -> None:
    line(pdf, 50, 505, 910, 505, LINE, 0.7)
    text(
        pdf,
        50,
        511,
        "SAJU DIARY ASSISTANT  ·  repository master 85e0171  ·  2026.09.04",
        7.2,
        MUTED,
    )
    right_text(pdf, 910, 511, f"{section}   {page} / 4", 7.2, MUTED, FONT_BOLD)


def slide_title(
    pdf: canvas.Canvas,
    number: str,
    title_value: str,
    subtitle: str,
    *,
    badge: str | None = None,
) -> None:
    text(pdf, 50, 40, number, 11, CYAN, FONT_BOLD)
    text(pdf, 82, 34, title_value, 25, TEXT, FONT_BOLD)
    text(pdf, 82, 70, subtitle, 10, MUTED)
    if badge:
        badge_width = pdfmetrics.stringWidth(badge, FONT_BOLD, 8) + 22
        pill(pdf, 910 - badge_width, 38, badge, fill=HexColor("#FBE9ED"), color=RED)


def model_card(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    width: float,
    label: str,
    headline: str,
    description: str,
    accent: Color,
    *,
    selected: bool = False,
) -> None:
    rect(pdf, x, top, width, 112, PANEL, radius=13, stroke=accent if selected else LINE, stroke_width=1.5)
    pdf.setFillColor(accent)
    pdf.roundRect(x, y_from_top(top, 112), 5, 112, 2.5, fill=1, stroke=0)
    text(pdf, x + 18, top + 15, label, 8, accent, FONT_BOLD)
    text(pdf, x + 18, top + 37, headline, 15, TEXT, FONT_BOLD)
    wrapped_text(pdf, x + 18, top + 65, description, width - 34, 8.8, color=MUTED, leading=13.2, max_lines=3)
    if selected:
        pill(pdf, x + width - 68, top + 12, "SELECT", fill=accent, color=BG, size=7, height=19)


def slide_one(pdf: canvas.Canvas) -> None:
    draw_background(pdf, 1)
    text(pdf, 50, 18, "특화형_4_멘티_김슬기_김민희", 14, TEXT, FONT_BOLD)
    pill(pdf, 50, 43, "MODEL TRAINING BRIEF", fill=HexColor("#E4F5F3"), color=CYAN, size=7.5)
    right_text(pdf, 910, 36, "K0 × MIX2K × LoRA", 9, MUTED, FONT_BOLD)

    text(pdf, 50, 78, "작은 모델을,", 32, TEXT, FONT_BOLD)
    text(pdf, 50, 119, "실서비스에 맞게 교정하다", 32, TEXT, FONT_BOLD)
    text(pdf, 50, 168, "1.3B 한국어 모델의 자연스러움은 보존하고, 구조화 사실 읽기만 정밀하게 교정", 12, MUTED)

    model_card(
        pdf,
        50,
        215,
        245,
        "STARTING POINT",
        "K0-INSTRUCT",
        "Kanana 2 · 1.291B · 한국어 대화 품질과 후속 설명 능력이 좋은 기준 모델",
        BLUE,
    )
    arrow(pdf, 300, 333, 271, CYAN)
    model_card(
        pdf,
        338,
        215,
        245,
        "GROUNDING ADAPTER",
        "LoRA r=16",
        "원국·기간 label을 정확히 읽고, 제공되지 않은 관계·예측을 만들지 않도록 교정",
        CYAN,
        selected=True,
    )

    rect(pdf, 620, 98, 290, 267, PANEL, radius=15, stroke=LINE)
    text(pdf, 642, 120, "모델 선정 기준", 15, TEXT, FONT_BOLD)
    criteria = [
        ("01", "자연스러운 한국어", "K0의 장점 유지", BLUE),
        ("02", "구조 사실 정확도", "JSON field 혼동 교정", CYAN),
        ("03", "단일 GPU 운용", "16 GB 환경에서 반복 실험", PURPLE),
    ]
    row_top = 158
    for index, (number, title_value, description, accent) in enumerate(criteria):
        current_top = row_top + index * 54
        pdf.setFillColor(accent)
        pdf.circle(658, y_from_top(current_top + 15), 14, fill=1, stroke=0)
        text(pdf, 650, current_top + 7, number, 7.5, BG, FONT_BOLD)
        text(pdf, 682, current_top + 2, title_value, 10.5, TEXT, FONT_BOLD)
        text(pdf, 682, current_top + 21, description, 8.2, MUTED)

    line(pdf, 642, 318, 888, 318, LINE)
    pill(pdf, 642, 331, "KI20 = 실패 비교 baseline", fill=HexColor("#FFF1DF"), color=ORANGE, size=7.5)

    rect(pdf, 50, 355, 533, 116, HexColor("#EAF8F6"), radius=15, stroke=HexColor("#A9DDD7"))
    text(pdf, 71, 376, "선정 결론", 9, CYAN, FONT_BOLD)
    text(pdf, 71, 401, "K0의 말하기 능력  +  LoRA의 근거 교정", 18, TEXT, FONT_BOLD)
    wrapped_text(
        pdf,
        71,
        435,
        "Full FT와 KI20 이어학습은 1차에서 제외 — 기반 능력 손실 위험과 비용을 줄이고 비교 가능성을 확보했습니다.",
        486,
        8.6,
        color=MUTED,
        leading=13,
        max_lines=2,
    )

    rect(pdf, 620, 382, 290, 89, PANEL_ALT, radius=15)
    text(pdf, 642, 399, "목표는 ‘사주 지식 추가’가 아닙니다", 10, YELLOW, FONT_BOLD)
    wrapped_text(
        pdf,
        642,
        424,
        "실제 runtime JSON에서 필요한 사실만 골라 일반인이 이해할 말로 설명하는 행동 교정입니다.",
        246,
        9.2,
        color=TEXT,
        leading=14,
        max_lines=3,
    )

    footer(pdf, 1, "MODEL")
    pdf.showPage()


def donut(pdf: canvas.Canvas, x: float, top: float, size: float, trainable_ratio: float) -> None:
    cx = x + size / 2
    cy = PAGE_H - top - size / 2
    radius = size / 2
    pdf.setFillColor(HexColor("#D9E4F1"))
    pdf.circle(cx, cy, radius, fill=1, stroke=0)
    pdf.setFillColor(CYAN)
    pdf.wedge(
        cx - radius,
        cy - radius,
        cx + radius,
        cy + radius,
        90,
        360 * trainable_ratio,
        fill=1,
        stroke=0,
    )
    pdf.setFillColor(BG_ALT)
    pdf.circle(cx, cy, radius * 0.69, fill=1, stroke=0)
    text(pdf, cx - 42, top + 89, "1.43%", 25, CYAN, FONT_BOLD)
    text(pdf, cx - 35, top + 126, "TRAINABLE", 8.5, MUTED, FONT_BOLD)


def rank_bar(
    pdf: canvas.Canvas,
    top: float,
    rank: str,
    params: str,
    ratio: str,
    bar_width: float,
    color: Color,
    *,
    primary: bool = False,
) -> None:
    text(pdf, 438, top + 1, rank, 10, TEXT, FONT_BOLD)
    rect(pdf, 490, top, 315, 18, HexColor("#E2EAF4"), radius=9)
    rect(pdf, 490, top, bar_width, 18, color, radius=9)
    text(pdf, 817, top + 1, f"{params} · {ratio}", 8.2, TEXT, FONT_BOLD)
    if primary:
        pill(pdf, 490 + bar_width - 44, top - 3, "PRIMARY", fill=CYAN, color=BG, size=6, height=17, padding_x=6)


def metric_card(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    width: float,
    value: str,
    label: str,
    accent: Color,
) -> None:
    rect(pdf, x, top, width, 72, PANEL, radius=13, stroke=LINE)
    text(pdf, x + 15, top + 13, value, 17, accent, FONT_BOLD)
    text(pdf, x + 15, top + 43, label, 8, MUTED)


def slide_two(pdf: canvas.Canvas) -> None:
    draw_background(pdf, 2)
    slide_title(
        pdf,
        "02",
        "경량화 방법 — 98.57%는 그대로",
        "Base weight를 freeze하고, 작은 adapter만 학습·저장·교체합니다.",
    )

    rect(pdf, 50, 111, 330, 270, PANEL, radius=16, stroke=LINE)
    text(pdf, 72, 131, "LoRA r=16 · 실제 파라미터", 11, TEXT, FONT_BOLD)
    donut(pdf, 92, 164, 196, 0.014256134)
    text(pdf, 106, 333, "18,677,760 / 1,310,156,032", 10.5, TEXT, FONT_BOLD)
    text(pdf, 132, 354, "Base 1.291B는 변경하지 않음", 8.5, MUTED)

    rect(pdf, 405, 111, 505, 270, PANEL, radius=16, stroke=LINE)
    text(pdf, 427, 131, "Rank ablation", 12, TEXT, FONT_BOLD)
    text(pdf, 805, 133, "α=32 고정", 8, MUTED, FONT_BOLD)
    rank_bar(pdf, 172, "r=8", "9.34M", "0.72%", 83, BLUE)
    rank_bar(pdf, 218, "r=16", "18.68M", "1.43%", 166, CYAN, primary=True)
    rank_bar(pdf, 264, "r=32", "37.36M", "2.81%", 315, PURPLE)

    settings = [
        ("all-linear", BLUE),
        ("RSLoRA", PURPLE),
        ("dropout 0.05", ORANGE),
        ("bias none", GREEN),
        ("adapter-only", CYAN),
    ]
    current_x = 428
    for label, accent in settings:
        used = pill(pdf, current_x, 318, label, fill=HexColor("#EDF3F8"), color=accent, size=7.2, height=21)
        current_x += used + 7
    text(pdf, 428, 351, "독립 adapter이므로 K0·r8·r16·r32를 같은 조건에서 비교 가능", 8.4, MUTED)

    metric_card(pdf, 50, 403, 198, "34분 59초", "R16 · 2K · 1 epoch", CYAN)
    metric_card(pdf, 264, 403, 198, "4.34 GiB", "학습 peak reserved", PURPLE)
    metric_card(pdf, 478, 403, 198, "2.69 GiB", "실제 추론 peak allocated", BLUE)
    metric_card(pdf, 692, 403, 218, "224 modules", "LoRA 적용 linear layer", ORANGE)

    footer(pdf, 2, "LORA")
    pdf.showPage()


def legend_item(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    color: Color,
    label: str,
    count: str,
) -> None:
    rect(pdf, x, top + 2, 9, 9, color, radius=2)
    text(pdf, x + 16, top, label, 7.8, TEXT, FONT_BOLD)
    right_text(pdf, x + 213, top, count, 7.8, MUTED, FONT_BOLD)


def recipe_item(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    value: str,
    label: str,
    accent: Color,
) -> None:
    text(pdf, x, top, value, 13, accent, FONT_BOLD)
    text(pdf, x, top + 23, label, 7.4, MUTED)


def slide_three(pdf: canvas.Canvas) -> None:
    draw_background(pdf, 3)
    slide_title(
        pdf,
        "03",
        "데이터셋 & 실제 학습 — 2,000행을 ‘실제 입력 형태’로",
        "compact 요약본이 아니라 production-like full runtime snapshot을 그대로 학습했습니다.",
    )

    rect(pdf, 50, 105, 860, 55, HexColor("#EAF8F6"), radius=13, stroke=HexColor("#A9DDD7"))
    text(pdf, 69, 120, "FULL RUNTIME SNAPSHOT", 8, CYAN, FONT_BOLD)
    text(
        pdf,
        69,
        139,
        "원국 4주  +  일간·오행·음양·십신·지장간  +  기간 연/월/일 간지  +  limitations·provenance",
        10.5,
        TEXT,
        FONT_BOLD,
    )

    rect(pdf, 50, 178, 500, 208, PANEL, radius=15, stroke=LINE)
    text(pdf, 70, 196, "학습 데이터 구성", 11, TEXT, FONT_BOLD)
    right_text(pdf, 530, 197, "TOTAL 2,000", 8.5, CYAN, FONT_BOLD)

    axes = [
        ("구조 사실", 300, CYAN),
        ("원국 설명", 300, BLUE),
        ("원국+오늘", 450, PURPLE),
        ("후속 질문", 300, ORANGE),
        ("상태·교정", 250, GREEN),
        ("일반·공감", 250, YELLOW),
        ("불확실성", 100, RED),
        ("HARD QA", 50, HexColor("#7F95B5")),
    ]
    bar_x = 70
    bar_top = 224
    bar_width = 460
    used_width = 0.0
    for label, count, color in axes:
        segment_width = bar_width * count / 2000
        rect(pdf, bar_x + used_width, bar_top, segment_width, 18, color, radius=0)
        used_width += segment_width

    for index, (label, count, color) in enumerate(axes):
        column = index % 2
        row = index // 2
        legend_item(
            pdf,
            70 + column * 235,
            258 + row * 26,
            color,
            label,
            f"{count:,} · {count / 20:g}%",
        )

    line(pdf, 70, 362, 530, 362, LINE)
    text(pdf, 70, 368, "600개 원국 · 300개 날짜 · 학습과 분리된 dev 200", 8, MUTED, FONT_BOLD)

    rect(pdf, 570, 178, 340, 208, PANEL, radius=15, stroke=LINE)
    text(pdf, 590, 196, "학습 recipe", 11, TEXT, FONT_BOLD)
    recipe_item(pdf, 590, 226, "2048", "max_length", CYAN)
    recipe_item(pdf, 682, 226, "1 epoch", "250 optimizer steps", PURPLE)
    recipe_item(pdf, 810, 226, "5e-5", "cosine LR", ORANGE)
    recipe_item(pdf, 590, 280, "1 × 8", "batch × grad accum", BLUE)
    recipe_item(pdf, 682, 280, "BF16", "gradient checkpoint", GREEN)
    recipe_item(pdf, 810, 280, "LOSS", "assistant only", YELLOW)
    line(pdf, 590, 329, 890, 329, LINE)
    text(pdf, 590, 340, "TOKEN AUDIT · MAX", 7.5, MUTED, FONT_BOLD)
    text(pdf, 590, 360, "prompt 1,802  ·  answer 169  ·  rendered 1,960", 8.4, TEXT, FONT_BOLD)

    pipeline_top = 408
    stages = [
        ("TEACHER", "초안", BLUE),
        ("VALIDATOR", "구조 fact", CYAN),
        ("PEER", "PASS", PURPLE),
        ("TOKEN", "0 truncation", GREEN),
        ("LORA", "1 epoch", ORANGE),
    ]
    start_x = 50
    stage_width = 146
    gap = 31
    for index, (label, detail, accent) in enumerate(stages):
        x = start_x + index * (stage_width + gap)
        rect(pdf, x, pipeline_top, stage_width, 52, PANEL, radius=11, stroke=accent)
        text(pdf, x + 13, pipeline_top + 10, label, 7.2, accent, FONT_BOLD)
        text(pdf, x + 13, pipeline_top + 27, detail, 9.2, TEXT, FONT_BOLD)
        if index < len(stages) - 1:
            arrow(pdf, x + stage_width + 5, x + stage_width + gap - 6, pipeline_top + 26, MUTED)

    text(
        pdf,
        50,
        470,
        "실험 한계 · 최종 draft는 Codex 2,000건, review는 Claude 191 + Codex 1,809 → cross-provider 계약 미충족, production 승격 금지",
        7.3,
        RED,
        FONT_BOLD,
    )

    footer(pdf, 3, "DATA + TRAIN")
    pdf.showPage()


def input_row(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    field: str,
    value: str,
    *,
    accent: Color,
) -> None:
    text(pdf, x, top, field, 8.2, MUTED, FONT_BOLD)
    text(pdf, x + 118, top - 1, value, 10.2, accent, FONT_BOLD)


def outcome_item(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    width: float,
    label: str,
    body: str,
    accent: Color,
) -> None:
    rect(pdf, x, top, width, 69, PANEL, radius=12, stroke=LINE)
    text(pdf, x + 14, top + 11, label, 7.5, accent, FONT_BOLD)
    wrapped_text(pdf, x + 14, top + 31, body, width - 28, 7.9, color=TEXT, leading=11.5, max_lines=3)


def slide_four(pdf: canvas.Canvas) -> None:
    draw_background(pdf, 4)
    slide_title(
        pdf,
        "04",
        "실사용 입·출력 — 정확한 구조에서 자연스러운 3줄로",
        "Dashboard v1.14 실제 GPU smoke · raw output 보존 · 자동 재작성 없음",
        badge="DIAGNOSTIC · production=false",
    )

    rect(pdf, 50, 109, 398, 286, PANEL, radius=15, stroke=LINE)
    pill(pdf, 70, 127, "REAL INPUT · 핵심 사실 발췌", fill=HexColor("#E4F5F3"), color=CYAN, size=7.3)
    text(pdf, 70, 164, "natal", 9, BLUE, FONT_BOLD)
    input_row(pdf, 82, 186, "year / month", "戊辰  /  甲子", accent=TEXT)
    input_row(pdf, 82, 209, "day / hour", "乙丑  /  壬午", accent=TEXT)
    input_row(pdf, 82, 232, "day master", "乙木", accent=CYAN)
    line(pdf, 70, 258, 428, 258, LINE)
    text(pdf, 70, 269, "period · 2026-09-02", 9, PURPLE, FONT_BOLD)
    input_row(pdf, 82, 292, "year / month", "丙午  /  丙申", accent=TEXT)
    input_row(pdf, 82, 315, "day", "己卯", accent=ORANGE)
    rect(pdf, 70, 345, 358, 35, HexColor("#EAF7F7"), radius=8)
    text(pdf, 82, 355, "Q. 오늘의 흐름을 원국과 함께 이야기해줘", 8.3, TEXT, FONT_BOLD)
    text(pdf, 70, 385, "실제 호출 778 input tokens · full snapshot은 더 많은 field 포함", 6.8, MUTED)

    arrow(pdf, 458, 481, 252, CYAN)

    rect(pdf, 490, 109, 420, 286, HexColor("#F2FBFA"), radius=15, stroke=CYAN, stroke_width=1.3)
    pill(pdf, 510, 127, "R16 RAW OUTPUT", fill=CYAN, color=BG, size=7.3)
    text(pdf, 510, 166, "원국에서 확인되는 일간은 乙이며,", 12.2, TEXT, FONT_BOLD)
    text(pdf, 510, 191, "일주는 乙丑입니다.", 12.2, TEXT, FONT_BOLD)
    text(pdf, 510, 230, "선택 날짜인 2026-09-02의 연간지는 丙午,", 10.6, TEXT)
    text(pdf, 510, 253, "월간지는 丙申, 일진은 己卯입니다.", 10.6, TEXT)
    text(pdf, 510, 286, "오늘의 흐름은 이 날짜 간지와 원국의 乙丑을", 9.3, TEXT)
    text(pdf, 510, 306, "함께 참고할 수 있지만, 두 정보를 합쳐 특정한", 9.3, TEXT)
    text(pdf, 510, 326, "사건이나 결과를 단정할 수는 없습니다.", 9.3, TEXT)
    line(pdf, 510, 348, 890, 348, HexColor("#B8DAD7"))
    pill(pdf, 510, 359, "16.7 s", fill=HexColor("#E9F2F8"), color=CYAN, size=7.2)
    pill(pdf, 575, 359, "peak 2.69 GiB", fill=HexColor("#E9F2F8"), color=BLUE, size=7.2)
    pill(pdf, 684, 359, "3 lines", fill=HexColor("#E9F2F8"), color=GREEN, size=7.2)
    pill(pdf, 752, 359, "no retry / rewrite", fill=HexColor("#E9F2F8"), color=ORANGE, size=7.2)

    outcome_item(pdf, 50, 416, 268, "정확해진 부분", "丙午=연간지 · 丙申=월간지 · 己卯=일진, 금지된 통근·신강약 생성 없음", GREEN)
    outcome_item(pdf, 334, 416, 268, "남은 release blocker", "원국 네 기둥 전체를 답변에서 생략 → warning, 진단 모델로만 유지", RED)
    outcome_item(pdf, 618, 416, 292, "실서비스 계약", "4K input + 4K output · native ≥8K · 후속 질문은 3줄·grounding PASS", CYAN)

    footer(pdf, 4, "RUNTIME")
    pdf.showPage()


def build_deck(output_path: Path) -> None:
    register_fonts()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(
        str(output_path),
        pagesize=(PAGE_W, PAGE_H),
        pageCompression=1,
        invariant=1,
    )
    pdf.setTitle("사주 다이어리 모델 선정·경량화·학습·실사용")
    pdf.setAuthor("saju_diary_assistant")
    pdf.setSubject("K0 기반 MIX2K LoRA R16 실험 발표자료")
    pdf.setKeywords("Kanana, K0, LoRA, MIX2K, grounding, dashboard")

    slide_one(pdf)
    slide_two(pdf)
    slide_three(pdf)
    slide_four(pdf)
    pdf.save()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="4장 모델 학습 발표자료 PDF를 생성합니다.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "docs/presentations/2026-09-04-model-training-overview/"
            "saju-model-training-overview-4p.pdf"
        ),
        help="생성할 PDF 경로",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_deck(args.output)
    print(f"created={args.output.resolve()}")
    print(f"bytes={args.output.stat().st_size}")


if __name__ == "__main__":
    main()
