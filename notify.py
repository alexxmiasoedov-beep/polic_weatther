"""Отправка сообщения в Telegram от имени @polic_weather_bot.

Токен: переменная окружения TELEGRAM_BOT_TOKEN (в репозитории не хранится).
chat_id: файл telegram_chat_id.txt в корне репо; если файла нет —
одноразовое обнаружение через getUpdates (владелец должен хотя бы раз
написать боту /start) с сохранением в файл.

Запуск: python notify.py "текст"     или     python notify.py --file report.txt
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CHAT_ID_FILE = Path(__file__).parent / "telegram_chat_id.txt"


def api(token: str, method: str, params: dict):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get_chat_id(token: str):
    if CHAT_ID_FILE.exists():
        return CHAT_ID_FILE.read_text().strip()
    upd = api(token, "getUpdates", {})
    for u in reversed(upd.get("result", [])):
        msg = u.get("message") or u.get("channel_post")
        if msg and msg.get("chat"):
            chat_id = str(msg["chat"]["id"])
            CHAT_ID_FILE.write_text(chat_id + "\n")
            print(f"chat_id {chat_id} обнаружен и сохранён в {CHAT_ID_FILE.name}")
            return chat_id
    return None


def send(token: str, text: str) -> bool:
    chat_id = get_chat_id(token)
    if not chat_id:
        print("ОШИБКА: chat_id неизвестен — владелец должен написать боту "
              "@polic_weather_bot любое сообщение (/start).", file=sys.stderr)
        return False
    # телеграм ограничивает сообщение 4096 символами — режем по частям
    ok = True
    for i in range(0, len(text), 4000):
        resp = api(token, "sendMessage", {
            "chat_id": chat_id, "text": text[i:i + 4000], "parse_mode": "HTML",
        })
        ok = ok and resp.get("ok", False)
        if not resp.get("ok"):
            print(f"ОШИБКА sendMessage: {resp}", file=sys.stderr)
    return ok


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Нет TELEGRAM_BOT_TOKEN в окружении")
    if len(sys.argv) >= 3 and sys.argv[1] == "--file":
        text = Path(sys.argv[2]).read_text(encoding="utf-8")
    elif len(sys.argv) >= 2:
        text = sys.argv[1]
    else:
        text = sys.stdin.read()
    sys.exit(0 if send(token, text.strip()) else 1)


if __name__ == "__main__":
    main()
