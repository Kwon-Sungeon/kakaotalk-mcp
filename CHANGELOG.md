# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
