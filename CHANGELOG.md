# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] - 2026-11-17

- **구형 입력란 안내 문구(`메시지 입력`) 즉시 교체 및 Windows 11 클립보드 안정화**: 구형 `RICHEDIT50W`에서 안내 문구(`메시지 입력`)가 문자열로 반환될 때 텍스트·이미지·파일·멘션 전송 전반에서 실제 사용자 초안과 구분해 중단 없이 처리하며(`_is_real_draft`, `replace_legacy_hint=True` 기본값), Windows 11 `CloseClipboard()` 합성 시 `HGLOBAL` 핸들 재할당 오탐 수정, `OpenClipboard` 일시적 접근 거부(`WinError 5`) 재시도(`_open_clipboard`), `GetClipboardOwner` null 예외 가드, 포커스 검증 하의 하드웨어 Enter 제출을 반영했습니다.
- **구형 Win32 OCR 검색·목록 및 오너드로우 파일·사진 팝업 자동 전송**: 구형 `SearchListCtrl`·`ChatRoomListCtrl`(`EVA_VH_ListControl_Dblclk`)과 버튼 컨트롤이 노출되지 않는 파일·사진 전송 확인 팝업에서 다중 모니터 DPI 배율 차이를 픽셀 단위로 자동 보정하는 `CaptureRendered` 및 Windows 한글 OCR(`ConfirmLegacyAttachmentPopup`, `Ctrl+V` 이미지 붙여넣기 폴백 포함)을 통해 닫힌 방 검색 열기, `kakao_list_chats`, 무터치 파일·이미지 전송까지 일관되게 처리합니다.
- **Qt UIA 성능 및 정확도 개선**: `qt_uia.ps1`에서 `KakaoUia` C# 헬퍼(`PropertyCondition`/`CacheRequest`), `InitializeOcr`/`InitializeForms` 1회 초기화, `send`의 `verify_bubble` 인워커 검증 및 폴링 대기(최대 400ms 버튼 대기 / 40ms 클리어·말풍선 확인)로 중복 왕복과 고정 대기 시간을 단축했습니다.
- **다국어·24시간제 및 파싱 보강**: Qt `Messages`의 영어(`AM/PM`)·24시간제·영문 날짜 구분선·파일명 null 안전성을 보완하고, `parser.py`에서 `파일:` 공백/용량 변형·균형 괄호 URL·추가 시스템 알림을 지원합니다.
- **구형 Win32 안정성·성능 개선**: `EnumWindows`/`EnumChildWindows` 콜백 예외 가드 추가, `@멘션` 비한글(영문·숫자·기호) `SendInput(KEYEVENTF_UNICODE)` 지원, 인프로세스 WIC 이미지 디코딩 및 `.webp` 폴백(`Windows.Graphics.Imaging`), 클립보드 복사 폴링을 개선했습니다.
- **전송 검증 및 모니터링 개선**: `delivery.py`의 중복 `resolve`/`verify` 및 Qt 텍스트 왕복을 제거하고, 구형 `complete` 모드 기준선 재시도 및 말풍선 확인 대기 여유(최대 4회 / 3.0초)와 스크롤 위치 자동 정렬·빈 방 확인(`at_end=True`)을 보강했습니다. `ChatMonitor` 날짜 구분선 이탈 오탐을 수정하고 `MonitorManager`로 다중 방 동시 모니터링을 지원합니다.
- **신규 도구 및 스크롤 페이지네이션**: `kakao_read_messages(..., scroll_pages=N)` 이전 대화 스크롤 읽기, `kakao_list_chats`, `kakao_list_participants`, `kakao_close_room`, `kakao_inspect_ui`(`owned_popups` 모달 트리 진단 포함)를 추가하고 `.ps1` 파일에 UTF-8 BOM을 적용했습니다.

## [0.7.0] - 2026-11-13

- 구형의 붙여넣기를 유지하면서 방 소속 입력란의 컨트롤 ID와 후보 개수를 검사합니다. 다른 검색어가 남아 있으면 보존합니다.
- 텍스트·멘션·사진·파일 전송 후 같은 방에 새로 추가된 말풍선을 확인합니다. 불분명한 전송은 기존 SQLite 기록을 통해 중복을 차단합니다.
- 구형 메시지 목록에 텍스트가 노출되지 않을 때 백그라운드 읽기에서 Windows 한글 OCR을 지원합니다. Qt의 나와의 채팅 검색과 고배율 화면의 클릭 위치를 보완했습니다.

## [0.6.1] - 2026-11-13

- 구형 텍스트 전송에서 클립보드를 닫은 뒤 게시된 데이터와 변경 번호를 확인합니다. 붙여넣기 전후의 실패 지점을 별도 오류 코드로 반환합니다.
- 구형 Rich Edit의 안내 문구가 실제 문자열로 남으면 방과 입력란을 다시 확인하고 클릭해 활성화합니다. 사용자가 빈 입력란을 확인한 단건 요청은 replace_legacy_hint로 동일한 안내 문구만 선택해 교체할 수 있습니다. 방 검증과 항목별 중복 전송 보호는 이 옵션에도 적용됩니다.

## [0.6.0] - 2026-11-12

- 외부 수정판 0.4.0+np.2의 구형 카카오톡 대응을 병합했습니다. 채팅 목록과 입력란을 함께 식별하고, 전송 버튼이 없는 빌드에서 기존 초안을 확인한 뒤 붙여넣기를 사용합니다.
- 구형 파일 첨부에 클립보드 경로를 추가했습니다. 첨부 방식을 바꿔도 v0.5.0의 항목별 중복·미확인 전송 보호가 적용됩니다.
- 모든 전송 도구에 dry_run과 선택적 전송 정책을 추가했습니다. kakao_get_send_log는 기존 SQLite 기록에서 본문 없이 항목별 상태를 반환합니다.
- 대화 시각·시스템 알림·링크 파싱과 필터, 모니터 이벤트 대기 및 누락 집계, 구형 계정 캐시 이미지 형식 판별을 개선했습니다.

## [0.5.0] - 2026-11-12

- 텍스트·멘션·사진·파일을 한 호출에서 방 검색, 대상 창 확인, 전송, 결과 기록까지 처리합니다. 구형에서 식별할 수 없는 후보나 사진 창에서는 중단합니다.
- 일괄 전송의 항목별 결과를 보존하고 재시작 후에도 동일 요청의 재전송을 방지합니다. `kakao_delivery_status`로 기록을 조회할 수 있습니다.
- 방 오류에 대한 Qt 전체 복사 재시도를 제거하고 도구 오류를 MCP 오류로 반환합니다.

## [0.4.0] - 2026-11-12

- PDF 등 일반 파일을 열린 방의 파일 선택 창으로 첨부하는 `kakao_send_file`을 추가했습니다. 결과가 불확실하면 자동 재시도하지 않습니다.
- 구형의 검색 입력란 선택과 전송 결과 확인을 수정했습니다. Ctrl+Enter 설정을 지원합니다.
- 신형에서 UIA 프로세스를 재사용하고 창·메시지 탐색 범위를 줄였습니다.
- 다른 PC에서 연결 상태와 실제 카카오톡 버전을 확인할 수 있는 `--diagnose` 명령을 추가했습니다.

## [0.3.0] - 2026-11-12

- 하나의 설치 파일에 구형 Win32 및 신형 Qt UIA 백엔드를 포함합니다. 실행 중인 카카오톡에 맞춰 백엔드를 자동 선택하며 두 버전 모두 MCP 도구 13개를 제공합니다.
- 최근 대화 조회와 모니터링에서 백그라운드 접근을 우선하고, 상세 조회에는 전체 복사를 사용할 수 있습니다.
- 방과 입력 대상 확인을 강화하고, Codex와 Claude Desktop의 UI 조작을 한 번에 하나씩 실행합니다.

## [0.2.0] - 2026-03-22

### Added
- `kakao_send_bulk` — 여러 채팅방에 동일 메시지를 한 번에 전송하는 일괄 전송 도구
  - `room_names` 리스트로 다수 채팅방 지정
  - `interval_sec` 파라미터로 방 간 전송 간격 조절 (기본 0.5초, 최소 0.3초)
  - 이미 열린 창은 재검색 없이 재활용하여 속도 향상
  - 개별 전송 결과를 room별로 반환
- `kakao_send_image` — 채팅방에 이미지 파일 전송 (CF_HDROP 클립보드 + Ctrl+V 방식)
  - JPG, PNG, GIF, BMP, WebP 형식 지원
  - 단일 또는 다수 이미지 순차 전송 지원
  - 카카오톡 전송 확인 다이얼로그 자동 처리

### Changed
- **타이밍 최적화**: 모든 하드코딩된 `time.sleep()` 값을 `config.py` 상수로 중앙 관리화
  - 채팅방 검색+열기: ~3.0초 → ~1.6초 (약 47% 단축)
  - 메시지 전송: ~0.35초 → ~0.16초 (약 54% 단축)
  - 3개 채팅방 전송 총합: ~10초 → ~5초 (약 50% 단축)
- `config.py`에 카테고리별 타이밍 상수 추가 (Window, Search, Image, Mention)
- `controller.py` 내 모든 `time.sleep()` 호출을 `config.상수`로 교체

## [0.1.0] - 2025-02-27

### Added

#### 기본 도구 (8개)
- `kakao_health_check` — 카카오톡 PC 실행 상태 확인 (윈도우 핸들, PID)
- `kakao_list_open_rooms` — 열려있는 채팅방 목록 조회
- `kakao_open_room` — 채팅방 검색 및 열기 (이미 열려있으면 포그라운드로)
- `kakao_send_message` — 채팅방에 메시지 전송 (클립보드 기반)
- `kakao_send_mention` — @멘션과 함께 메시지 전송 (한글 2벌식 키보드 시뮬레이션)
- `kakao_read_messages` — 채팅방 메시지 읽기 (Ctrl+A, Ctrl+C 클립보드 복사)
- `kakao_extract_links` — 채팅방에서 공유된 URL 추출
- `kakao_download_images` — 카카오톡 로컬 캐시에서 최근 이미지 다운로드

#### 모니터링 도구 (3개)
- `kakao_start_monitor` — 채팅방 키워드 모니터링 시작 (백그라운드 스레드)
- `kakao_stop_monitor` — 모니터링 중지
- `kakao_get_monitor_events` — 감지된 키워드 이벤트 조회 (트리거 메시지 + 최근 컨텍스트)

#### 인프라
- Win32 API 기반 카카오톡 PC 자동화 (`controller.py`)
- 카카오톡 클립보드 텍스트 파서 (`parser.py`)
- 한글 2벌식 자판 분해 및 키 입력 시뮬레이션 (초성/중성/종성 + 복합 자모)
- `pyproject.toml` 기반 패키지 구성 (`uvx kakaotalk-mcp` 실행 지원)
- 파서 단위 테스트 및 컨트롤러 mock 테스트 (28개)

### Fixed
- Windows 11에서 `SetForegroundWindow` 예외 처리
- 채팅방 검색 시 기존 검색어 잔류 문제 (ESC로 초기화)
- 메시지 전송 시 클립보드 충돌 방지 (`WM_SETTEXT` → 클립보드 `Ctrl+V` 방식)

[0.2.0]: https://github.com/kronenz/kakaotalk-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/kronenz/kakaotalk-mcp/releases/tag/v0.1.0

[0.3.0]: https://github.com/Kwon-Sungeon/kakaotalk-mcp/releases/tag/v0.3.0
