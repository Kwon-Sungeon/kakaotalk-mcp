# -*- coding: utf-8 -*-
"""Google Sheets 기반 카카오톡 DM 전송기.

시트(또는 CSV)에서 대상 목록 + 메시지 템플릿을 읽어, 단체방 참여자에게
1:1 메시지를 보내고 (필요시 친구추가 후), 전송 결과를 로그 시트에 기록한다.

사용법:
    # CSV 모드 (시트를 CSV로 내보낸 파일 사용)
    python sheet_sender.py --csv targets.csv --room "미드미♡" --dry-run

    # Google Sheets 모드 (서비스 계정 JSON 필요: pip install gspread)
    python sheet_sender.py --sheet <스프레드시트ID> --creds service_account.json --room "미드미♡"

시트/CSV 형식 (1행 = 헤더):
    이름, 메시지템플릿(선택), 기타컬럼...
    - '이름' 컬럼 필수 (참여자 표시 이름)
    - 템플릿에 {이름} {컬럼명} 플레이스홀더 사용 가능
    - --template 인자로 공통 템플릿 지정 가능 (행별 템플릿보다 우선순위 낮음)

전송 로그: 시트 모드면 '전송로그' 워크시트에 append, CSV 모드면 send_log.csv 에 append.
"""
import os
import sys
import csv
import time
import argparse
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kakao_mcp import controller, participants  # noqa: E402

LOG_HEADERS = ["일시", "채팅방", "이름", "메시지", "성공", "상세"]


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

def load_from_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_from_sheet(sheet_id, creds_path, worksheet=None):
    import gspread
    gc = gspread.service_account(filename=creds_path)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(worksheet) if worksheet else sh.sheet1
    return sh, ws.get_all_records()


def append_log_sheet(sh, row):
    try:
        ws = sh.worksheet("전송로그")
    except Exception:
        ws = sh.add_worksheet("전송로그", rows=1000, cols=10)
        ws.append_row(LOG_HEADERS)
    ws.append_row(row)


def append_log_csv(path, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(LOG_HEADERS)
        w.writerow(row)


# ---------------------------------------------------------------------------
# Message composition
# ---------------------------------------------------------------------------

def compose(template, row):
    msg = template
    for k, v in row.items():
        msg = msg.replace("{" + str(k).strip() + "}", str(v))
    return msg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="카카오톡 시트 기반 DM 전송기")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="대상 목록 CSV 파일")
    src.add_argument("--sheet", help="Google 스프레드시트 ID")
    ap.add_argument("--creds", help="gspread 서비스 계정 JSON 경로")
    ap.add_argument("--worksheet", help="워크시트 이름 (기본: 첫 시트)")
    ap.add_argument("--room", required=True, help="단체 채팅방 이름 (예: 미드미♡)")
    ap.add_argument("--template", default="", help="공통 메시지 템플릿")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="전송 간 대기 초 (기본 5초, 최소 3초)")
    ap.add_argument("--no-add-friend", action="store_true",
                    help="친구추가 폴백 비활성화")
    ap.add_argument("--close-after", action="store_true",
                    help="전송 후 1:1 창 닫기")
    ap.add_argument("--dry-run", action="store_true",
                    help="실제 전송 없이 대상/메시지만 출력")
    ap.add_argument("--limit", type=int, default=0, help="최대 전송 건수 (0=무제한)")
    args = ap.parse_args()

    # 카카오톡 실행 확인
    if not args.dry_run and not controller.is_kakaotalk_running()["running"]:
        print("[!] 카카오톡 PC가 실행 중이 아닙니다.")
        sys.exit(1)

    # 데이터 로드
    sh = None
    if args.csv:
        rows = load_from_csv(args.csv)
    else:
        if not args.creds:
            print("[!] --sheet 모드에는 --creds 서비스계정 JSON이 필요합니다.")
            sys.exit(1)
        sh, rows = load_from_sheet(args.sheet, args.creds, args.worksheet)

    if not rows:
        print("[!] 대상 데이터가 비어 있습니다.")
        sys.exit(1)
    if "이름" not in rows[0]:
        print(f"[!] '이름' 컬럼이 없습니다. 컬럼: {list(rows[0].keys())}")
        sys.exit(1)

    if args.limit:
        rows = rows[: args.limit]

    interval = max(3.0, args.interval)
    log_csv = os.path.join(os.path.dirname(os.path.abspath(
        args.csv or "send_log.csv")), "send_log.csv")

    print(f"== 대상 {len(rows)}명 / 방 '{args.room}' / 간격 {interval}s "
          f"/ dry-run={args.dry_run} ==")

    sent = failed = 0
    for i, row in enumerate(rows):
        name = str(row["이름"]).strip()
        if not name:
            continue
        template = str(row.get("메시지템플릿") or args.template).strip()
        if not template:
            print(f"[{i+1}] {name}: 템플릿 없음 — 건너뜀")
            continue
        msg = compose(template, row)

        if args.dry_run:
            print(f"[{i+1}] {name} <- {msg[:60]}{'...' if len(msg) > 60 else ''}")
            continue

        print(f"[{i+1}/{len(rows)}] {name} 에게 전송 중...")
        try:
            r = participants.dm_member(
                args.room, name, msg,
                add_friend=not args.no_add_friend,
                close_after=args.close_after,
            )
        except Exception as e:
            r = {"success": False, "message": str(e)}

        ok = bool(r.get("success"))
        detail = r.get("message", "") + " | " + "; ".join(r.get("steps", []))
        sent += ok
        failed += (not ok)
        print(f"    -> {'성공' if ok else '실패'}: {detail[:120]}")

        log_row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   args.room, name, msg, "O" if ok else "X", detail[:300]]
        if sh is not None:
            try:
                append_log_sheet(sh, log_row)
            except Exception as e:
                print(f"    (시트 로그 실패: {e})")
        append_log_csv(log_csv, log_row)

        if i < len(rows) - 1:
            time.sleep(interval)

    print(f"== 완료: 성공 {sent} / 실패 {failed} ==")
    if not args.dry_run:
        print(f"로그: {log_csv}" + (" 및 '전송로그' 워크시트" if sh else ""))


if __name__ == "__main__":
    main()
