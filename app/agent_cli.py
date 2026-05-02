from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta

from app.reminder_store import create_reminder, delete_reminder, due_reminders, list_reminders


def parse_time_expression(text: str, now: datetime | None = None) -> tuple[str, str]:
    current = now or datetime.now()
    clean_text = text.strip()
    hour_match = re.search(r"(\d{1,2})[:点](\d{1,2})?", clean_text)
    days = 1 if "tomorrow" in clean_text.lower() or "明天" in clean_text else 0

    remind_at = current + timedelta(days=days)
    if hour_match:
        hour = int(hour_match.group(1))
        minute = int(hour_match.group(2) or 0)
        remind_at = remind_at.replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif "一小时" in clean_text or "1小时" in clean_text:
        remind_at = current + timedelta(hours=1)
    elif "十分钟" in clean_text or "10分钟" in clean_text:
        remind_at = current + timedelta(minutes=10)
    else:
        remind_at = current + timedelta(minutes=30)

    task_text = re.sub(r"(明天|tomorrow|早上|上午|下午|晚上|\d{1,2}[:点]\d{0,2})", "", clean_text, flags=re.I).strip()
    task_text = task_text or clean_text
    return task_text, remind_at.isoformat(timespec="minutes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local agent CLI reminder assistant.")
    sub = parser.add_subparsers(dest="command", required=True)

    add_parser = sub.add_parser("add", help="Add a natural-language reminder.")
    add_parser.add_argument("text", nargs="+")

    sub.add_parser("list", help="List active reminders.")

    delete_parser = sub.add_parser("delete", help="Delete a reminder by id.")
    delete_parser.add_argument("id")

    sub.add_parser("run", help="Print due reminders and mark them done.")

    args = parser.parse_args()
    if args.command == "add":
        task_text, remind_at = parse_time_expression(" ".join(args.text))
        reminder = create_reminder(task_text, remind_at)
        print(f"created {reminder['id']} at {reminder['remind_at']}: {reminder['text']}")
    elif args.command == "list":
        for item in list_reminders():
            print(f"{item['id']} {item['remind_at']} {item['text']}")
    elif args.command == "delete":
        print("deleted" if delete_reminder(args.id) else "not found")
    elif args.command == "run":
        now_iso = datetime.now().isoformat(timespec="minutes")
        for item in due_reminders(now_iso):
            print(f"REMINDER {item['id']}: {item['text']}")


if __name__ == "__main__":
    main()
