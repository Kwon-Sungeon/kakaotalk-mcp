# 카카오톡 DM 전송기 — 추가 기능 안내

기존 kronenz/kakaotalk-mcp에 다음이 추가된 버전입니다.

## 추가된 MCP 도구

| 도구 | 설명 |
| --- | --- |
| `kakao_list_participants` | 단체방 사이드 드로어(≡)를 열어 참여자 목록 추출 |
| `kakao_dm_member` | 참여자에게 1:1 DM (친구면 검색으로 바로, 아니면 프로필 → 친구추가 → 1:1 채팅) |
| `kakao_open_member_profile` | 참여자 프로필 팝업 열기 (디버그/수동 단계) |
| `kakao_inspect_ui` | Win32 + UIA 컨트롤 트리 덤프 (좌표/버튼명 보정용) |
| `kakao_close_popups` | ESC로 팝업/드로어 닫기 |

## 추가된 파일

- `src/kakao_mcp/participants.py` — 참여자/프로필/DM 로직
- `scripts/sheet_sender.py` — Google Sheets/CSV 기반 일괄 전송기
- `config.py` 하단 — 드로어 버튼 좌표 등 보정 상수

## 설치

```bat
cd kakaotalk-mcp
pip install -e .
pip install uiautomation
pip install gspread        # Google Sheets 직접 연동 시에만
```

## MCP 설정 (로컬 소스 버전으로 교체)

`%APPDATA%\Claude\claude_desktop_config.json` (또는 Cowork MCP 설정)의 kakao 항목을:

```json
{
  "mcpServers": {
    "kakao": {
      "command": "python",
      "args": ["<설치경로>/kakaotalk-mcp/src/kakao_mcp/server.py"],
      "env": { "PYTHONPATH": "<설치경로>/kakaotalk-mcp/src" }
    }
  }
}
```

설정 후 앱 재시작 → 새 도구 5개가 보이면 성공.

## 전송기 사용

```bat
:: 1) 먼저 dry-run으로 확인
python scripts\sheet_sender.py --csv targets.csv --room "미드미♡" --template "안녕하세요 {이름}님!" --dry-run

:: 2) 실제 전송 (5초 간격)
python scripts\sheet_sender.py --csv targets.csv --room "미드미♡" --template "안녕하세요 {이름}님!" --interval 5
```

CSV 형식: 첫 행 헤더, `이름` 컬럼 필수. `{이름}`, `{컬럼명}` 플레이스홀더 지원.
Google Sheets 직접 연동: `--sheet <ID> --creds service_account.json` (전송 결과가 '전송로그' 워크시트에 자동 기록).

## 보정(캘리브레이션) 절차 — 최초 1회

카카오톡 UI는 버전마다 달라 첫 실행에서 보정이 필요할 수 있습니다.

1. 미드미♡ 방을 열어둔 상태에서 `kakao_inspect_ui("미드미♡")` 호출
2. 드로어를 수동으로 연 뒤 다시 호출 → 참여자 리스트가 UIA에 노출되는지 확인
3. 안 보이면 `config.py`의 `DRAWER_BUTTON_OFFSET_X/Y` 조정
4. 프로필 팝업의 버튼 이름('1:1 채팅', '친구 추가')이 다르면 `participants.py`의 `dm_member` 내 버튼명 리스트 수정

## 주의

- 짧은 시간에 다수에게 동일 메시지를 보내면 카카오톡 정책상 일시적 이용 제한을 받을 수 있습니다. 간격을 5초 이상으로 유지하고, 한 번에 수십 명 이상 보내지 마세요.
- 전송 중 카카오톡 창이 계속 최전면으로 올라오므로 PC를 다른 작업에 쓰지 않는 것이 좋습니다.
- 항상 `--dry-run`으로 먼저 검증하세요.
