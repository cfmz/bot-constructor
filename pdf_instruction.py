"""
Генерирует короткую PDF-инструкцию для пользователя (дублирует README.txt
в приятном виде, кладётся в ZIP рядом с кодом).
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT
import io

STEPS = [
    ("Шаг 1. Получи токен бота",
     "Открой Telegram, найди @BotFather, отправь команду /newbot, "
     "придумай имя и username (должен заканчиваться на \"bot\"). "
     "BotFather пришлёт токен — скопируй его."),
    ("Шаг 2. Настрой проект",
     "Переименуй файл .env.example в .env и вставь туда свой токен "
     "после BOT_TOKEN="),
    ("Шаг 3. Установи зависимости",
     "Открой терминал в папке с ботом (нужен Python 3.10+) и выполни: "
     "pip install -r requirements.txt"),
    ("Шаг 4. Запусти бота",
     "В терминале выполни: python bot.py — бот запущен и отвечает в Telegram."),
    ("Что дальше",
     "Чтобы бот работал 24/7, залей папку на GitHub и подключи к "
     "Render.com (Background Worker, бесплатный тариф) или другому "
     "Python-хостингу. Команда запуска: python bot.py"),
]


def build_pdf(bot_name: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleRu", parent=styles["Title"], alignment=TA_LEFT, fontSize=20,
    )
    heading_style = ParagraphStyle(
        "HeadingRu", parent=styles["Heading2"], spaceBefore=14, spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "BodyRu", parent=styles["BodyText"], leading=16,
    )

    story = [
        Paragraph(f"Инструкция по запуску бота «{bot_name}»", title_style),
        Spacer(1, 10 * mm),
    ]
    for heading, text in STEPS:
        story.append(Paragraph(heading, heading_style))
        story.append(Paragraph(text, body_style))

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        "Если что-то не работает — проверь, что файл называется именно "
        ".env (без .example), а токен вставлен без пробелов. Полная "
        "версия инструкции есть также в файле README.txt внутри архива.",
        body_style,
    ))

    doc.build(story)
    return buf.getvalue()
