#!/usr/bin/env python3
"""
狛江市施設予約システム 体育館空き状況チェッカー
https://k5.p-kashikan.jp/komae-city/index.php
"""

import re
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from collections import defaultdict

BASE_URL = "https://k5.p-kashikan.jp/komae-city/index.php"

# 体育館を持つ施設のみ（デフォルト対象）
GYM_FACILITIES = {
    "003": "狛江市民総合体育館",
    "005": "狛江市立狛江第一小学校",
    "007": "上和泉地域センター",
    "013": "狛江市立狛江第三小学校",
    "014": "狛江市立狛江第五小学校",
    "015": "狛江市立狛江第六小学校",
    "016": "狛江市立和泉小学校",
    "017": "狛江市立緑野小学校",
    "018": "狛江市立狛江第一中学校",
    "019": "狛江市立狛江第二中学校",
    "020": "狛江市立狛江第三中学校",
    "021": "狛江市立狛江第四中学校",
    "022": "西和泉体育館",
}

ALL_FACILITIES = {
    "001": "中央公民館",
    "002": "西河原公民館",
    "003": "狛江市民総合体育館",
    "004": "元和泉市民テニスコート",
    "005": "狛江市立狛江第一小学校",
    "006": "岩戸地域センター",
    "007": "上和泉地域センター",
    "008": "南部地域センター",
    "009": "野川地域センター",
    "010": "和泉多摩川地区センター",
    "011": "根川地区センター",
    "012": "谷戸橋地区センター",
    "013": "狛江市立狛江第三小学校",
    "014": "狛江市立狛江第五小学校",
    "015": "狛江市立狛江第六小学校",
    "016": "狛江市立和泉小学校",
    "017": "狛江市立緑野小学校",
    "018": "狛江市立狛江第一中学校",
    "019": "狛江市立狛江第二中学校",
    "020": "狛江市立狛江第三中学校",
    "021": "狛江市立狛江第四中学校",
    "022": "西和泉体育館",
    "023": "西和泉グランド",
    "024": "狛江市民グランド",
    "025": "多摩川緑地公園グランド",
    "026": "東野川市民テニスコート",
    "027": "元和泉市民運動ひろば",
    "028": "狛江市あいとぴあセンター",
}

# 体育館室とみなすキーワード（部屋名に含まれるもの）
GYM_KEYWORDS = ("体育",)

AVAILABLE_COLORS = {
    "#d1fafa": "空き(ネット予約可)",
    "#ffff66": "抽選申込受付中",
}

BOOKED_COLOR = "#ffe0c1"
CLOSED_COLOR = "#90ee90"


def parse_time(time_str: str) -> str:
    """'09001100' → '09:00-11:00'"""
    if len(time_str) == 8:
        return f"{time_str[:2]}:{time_str[2:4]}-{time_str[4:6]}:{time_str[6:8]}"
    return time_str


def fetch_page(shisetsu_code: str, date: datetime) -> str:
    data = urllib.parse.urlencode({
        "op": "srch_sst",
        "ShisetsuCode": shisetsu_code,
        "UseDate": date.strftime("%Y%m%d"),
        "UseYM": date.strftime("%Y%m"),
        "UseDay": date.strftime("%d"),
    }).encode()
    req = urllib.request.Request(
        BASE_URL, data=data,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_availability(html: str, date: datetime, room_filter: tuple[str, ...] | None = None) -> list[dict]:
    """HTMLから空きスロットを抽出。room_filter に含まれるキーワードの室場のみ対象。"""
    results = []
    time_re = re.compile(r"setAppStatus\([^,]+,\s*'[^']+',\s*\d+,\s*'(\d{8})'")

    cell_re = re.compile(
        r'<td\s+id="([^"#]+#[^"#]+#\d+)"\s+style="[^"]*background-color:([^;]+);[^"]*"(.*?)</td>',
        re.DOTALL,
    )
    name_row_re = re.compile(
        r'class="name">([^<]+)</td>(.*?)</tr>', re.DOTALL
    )
    id_re = re.compile(r'id="([^#"]+#[^#"]+#\d+)"')

    # 室場コード → 室場名
    room_names: dict[str, str] = {}
    for nm in name_row_re.finditer(html):
        room_name = nm.group(1).strip()
        for idm in id_re.finditer(nm.group(2)):
            code_part = idm.group(1).split("#")[0]
            room_names[code_part] = room_name
            break

    for m in cell_re.finditer(html):
        cell_id = m.group(1)
        bg_color = m.group(2).strip().lower()
        rest = m.group(3)

        if bg_color in (BOOKED_COLOR, CLOSED_COLOR):
            continue
        if bg_color not in AVAILABLE_COLORS:
            continue

        # セル内テキストは開始タグの ">" の後
        gt_pos = rest.rfind(">")
        content = rest[gt_pos + 1:].strip() if gt_pos != -1 else rest.strip()
        if content not in ("○", "〇", "●", "抽選"):
            continue

        code_part = cell_id.split("#")[0]
        room_name = room_names.get(code_part, code_part)

        # 室場名フィルタ
        if room_filter and not any(kw in room_name for kw in room_filter):
            continue

        time_match = time_re.search(rest)
        time_str = parse_time(time_match.group(1)) if time_match else "時間不明"

        results.append({
            "date": date,
            "time": time_str,
            "room": room_name,
            "status": AVAILABLE_COLORS[bg_color],
        })

    return results


def fetch_all(
    days: int = 60,
    facility_codes: list[str] | None = None,
    room_filter: tuple[str, ...] | None = GYM_KEYWORDS,
) -> dict:
    """全施設の空き状況を取得して返す。

    Returns:
        {
          "003": {
            "2026/05/20": [{"time": "13:50-16:05", "room": "...", "status": "..."}, ...]
          }, ...
        }
    """
    if facility_codes is None:
        facility_codes = list(GYM_FACILITIES.keys())

    today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today + timedelta(days=1)
    result: dict = {}

    for fcode in facility_codes:
        fname = ALL_FACILITIES.get(fcode, fcode)
        by_date: dict = {}

        for i in range(days):
            target = start + timedelta(days=i)
            try:
                html = fetch_page(fcode, target)
                slots = parse_availability(html, target, room_filter=room_filter)
                if slots:
                    date_key = target.strftime("%Y/%m/%d")
                    by_date[date_key] = [
                        {"time": s["time"], "room": s["room"], "status": s["status"]}
                        for s in slots
                    ]
            except Exception as e:
                print(f"[{fname} {target.strftime('%m/%d')}] エラー: {e}", file=sys.stderr)

        if by_date:
            result[fcode] = by_date

    return result


def check_availability(
    days: int = 60,
    facility_codes: list[str] | None = None,
    room_filter: tuple[str, ...] | None = GYM_KEYWORDS,
):
    if facility_codes is None:
        facility_codes = list(GYM_FACILITIES.keys())

    today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today + timedelta(days=1)

    filter_label = f"室場フィルタ: {room_filter}" if room_filter else "室場フィルタ: なし(全室場)"
    print(f"体育館空き状況 ({start.strftime('%Y/%m/%d')} から {days} 日間 / {filter_label})\n")

    data = fetch_all(days=days, facility_codes=facility_codes, room_filter=room_filter)

    if not data:
        print("全施設で空きスロットなし")
        return

    for fcode, by_date in data.items():
        fname = ALL_FACILITIES.get(fcode, fcode)
        print(f"{'='*55}")
        print(f"  {fname}")
        print(f"{'='*55}")
        for date_key in sorted(by_date.keys()):
            dt = datetime.strptime(date_key, "%Y/%m/%d")
            weekday = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
            print(f"\n  {date_key}({weekday})")
            for s in sorted(by_date[date_key], key=lambda x: (x["room"], x["time"])):
                print(f"    {s['time']}  {s['room']}  [{s['status']}]")
        print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="狛江市 体育館空き状況チェッカー")
    parser.add_argument("--days", type=int, default=60,
                        help="何日先まで調べるか (デフォルト: 60)")
    parser.add_argument("--facility", default="all",
                        help="施設コードをカンマ区切りで指定、またはall (例: 003,022,018)")
    parser.add_argument("--all-rooms", action="store_true",
                        help="体育館以外の室場も表示する")
    args = parser.parse_args()

    if args.facility == "all":
        targets = list(ALL_FACILITIES.keys())
    else:
        targets = [c.strip().zfill(3) for c in args.facility.split(",")]

    room_filter = None if args.all_rooms else GYM_KEYWORDS
    check_availability(days=args.days, facility_codes=targets, room_filter=room_filter)
