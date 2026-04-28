#!/usr/bin/env python3
"""
毎日 cron で実行するスクリプト。
前回の結果と比較して新規キャンセル枠が出たらメール通知する。

設定: config.json を同じディレクトリに置く（config.json.example を参照）
cron 例: 0 8 * * * /usr/bin/python3 /path/to/daily_check.py >> /var/log/komae_gym.log 2>&1
"""

import json
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from checker import ALL_FACILITIES, GYM_KEYWORDS, fetch_all

BASE_DIR = Path(__file__).parent
CACHE_FILE = BASE_DIR / "cache.json"
CONFIG_FILE = BASE_DIR / "config.json"


def load_config() -> dict:
    # 環境変数が設定されていればそちらを優先（GitHub Actions 用）
    if os.environ.get("SMTP_USER"):
        return {
            "from": os.environ["SMTP_FROM"],
            "to": os.environ["SMTP_TO"],
            "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
            "smtp_ssl": os.environ.get("SMTP_SSL", "false").lower() == "true",
            "smtp_user": os.environ["SMTP_USER"],
            "smtp_pass": os.environ["SMTP_PASS"],
            "days": int(os.environ.get("CHECK_DAYS", "60")),
        }
    # ローカル実行用
    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} が見つかりません。config.json.example を参考に作成してください。", file=sys.stderr)
        sys.exit(1)
    with CONFIG_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    with CACHE_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def save_cache(data: dict):
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_new_slots(old: dict, new: dict) -> dict:
    """前回にはなかった新規の空きスロットを返す。

    old / new の構造:
      { "_run_date": "YYYY/MM/DD", facility_code: { "YYYY/MM/DD": [...] } }

    前回の実行日 + days のウィンドウ内にあった日付に新規スロットが出た場合のみ通知する。
    ウィンドウ外（新たに追加された最終日）は通知しない。
    """
    new_slots: dict = {}

    # 前回の実行日とチェック日数からウィンドウ終端を算出
    old_run_date_str = old.get("_run_date")
    if old_run_date_str:
        old_run_date = datetime.strptime(old_run_date_str, "%Y/%m/%d")
        old_window_end = old_run_date + timedelta(days=old.get("_days", 60))
    else:
        old_window_end = None

    for fcode, by_date in new.items():
        if fcode.startswith("_"):
            continue
        old_by_date = old.get(fcode, {})
        for date_key, slots in by_date.items():
            old_slots = old_by_date.get(date_key)

            if old_slots is None:
                # 前回キャッシュにない日付 → ウィンドウ内なら本物のキャンセル、外なら受付開始
                if old_window_end is None:
                    continue
                slot_date = datetime.strptime(date_key, "%Y/%m/%d")
                if slot_date > old_window_end:
                    continue  # 新たにウィンドウに入った日 = 受付開始なので通知しない
                new_entries = slots  # ウィンドウ内の新規空き = キャンセル
            else:
                # 前回もあった日付 → 増えたスロットだけ通知
                old_set = {(s["time"], s["room"]) for s in old_slots}
                new_entries = [s for s in slots if (s["time"], s["room"]) not in old_set]

            if new_entries:
                new_slots.setdefault(fcode, {})[date_key] = new_entries

    return new_slots


def format_body(new_slots: dict, run_at: str) -> str:
    lines = [
        f"狛江市体育館 キャンセル枠通知",
        f"取得日時: {run_at}",
        "",
        "以下の枠が新たに空きになりました（キャンセルの可能性）:",
        "",
    ]
    for fcode in sorted(new_slots.keys()):
        fname = ALL_FACILITIES.get(fcode, fcode)
        lines.append(f"■ {fname}")
        by_date = new_slots[fcode]
        for date_key in sorted(by_date.keys()):
            dt = datetime.strptime(date_key, "%Y/%m/%d")
            weekday = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
            lines.append(f"  {date_key}({weekday})")
            for s in sorted(by_date[date_key], key=lambda x: (x["room"], x["time"])):
                lines.append(f"    {s['time']}  {s['room']}  [{s['status']}]")
        lines.append("")
    lines.append("予約はこちら: https://k5.p-kashikan.jp/komae-city/index.php")
    return "\n".join(lines)


def send_email(cfg: dict, subject: str, body: str):
    msg = MIMEMultipart()
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    host = cfg.get("smtp_host", "smtp.gmail.com")
    port = cfg.get("smtp_port", 587)
    use_ssl = cfg.get("smtp_ssl", False)

    if use_ssl:
        with smtplib.SMTP_SSL(host, port) as server:
            server.login(cfg["smtp_user"], cfg["smtp_pass"])
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg["smtp_user"], cfg["smtp_pass"])
            server.send_message(msg)


def main():
    cfg = load_config()
    run_at = datetime.now().strftime("%Y/%m/%d %H:%M")

    days = cfg.get("days", 60)
    print(f"[{run_at}] 空き状況を取得中...")
    new_data = fetch_all(days=days, room_filter=GYM_KEYWORDS)

    # 実行日と日数をキャッシュに記録（差分判定に使用）
    new_data["_run_date"] = datetime.now().strftime("%Y/%m/%d")
    new_data["_days"] = days

    old_data = load_cache()
    save_cache(new_data)

    if not old_data:
        # 初回はキャッシュがないので全空き枠をそのまま通知（メタキーを除く）
        new_slots = {
            fcode: by_date
            for fcode, by_date in new_data.items()
            if not fcode.startswith("_")
        }
    else:
        new_slots = find_new_slots(old_data, new_data)

    facility_data = {k: v for k, v in new_data.items() if not k.startswith("_")}

    if not new_slots:
        total = sum(len(slots) for by_date in facility_data.values() for slots in by_date.values())
        print(f"新規キャンセルなし（現在の空き: {total}件）")
        return

    count = sum(len(slots) for by_date in new_slots.values() for slots in by_date.values())
    print(f"新規キャンセル {count} 枠を検出。メール送信中...")

    subject = f"【狛江体育館】キャンセル枠 {count} 件 ({run_at})"
    body = format_body(new_slots, run_at)
    send_email(cfg, subject, body)
    print("メール送信完了。")


if __name__ == "__main__":
    main()
