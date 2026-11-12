# KakaoTalk MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Windows](https://img.shields.io/badge/platform-Windows-0078d4.svg)](https://www.microsoft.com/windows)

Windows에서 카카오톡 PC를 제어하는 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 서버입니다.

Codex, Claude Desktop, Claude Code 등 MCP 클라이언트에서 카카오톡 메시지를 보내고, 읽고, 채팅방을 관리할 수 있습니다.

신형 Qt 클라이언트는 Windows UI Automation(UIA), 구형 클라이언트는 Win32 컨트롤로 자동 선택됩니다. Qt 구현은 카카오톡 PC 26.9.2.447 (Qt 6.10.3)의 한국어 UI를 기준으로 합니다.

## 주요 기능

### 기본 도구

| 도구 | 설명 |
|------|------|
| `kakao_health_check` | 카카오톡 PC 실행 상태 확인 |
| `kakao_list_open_rooms` | 열려있는 채팅방 목록 조회 |
| `kakao_open_room` | 채팅방 검색 및 열기 |
| `kakao_send_message` | 채팅방에 메시지 전송 |
| `kakao_send_bulk` | 여러 채팅방에 동일 메시지 일괄 전송 |
| `kakao_send_mention` | @멘션과 함께 메시지 전송 |
| `kakao_read_messages` | 채팅방 메시지 읽기 |
| `kakao_extract_links` | 채팅방에서 공유된 URL 추출 |
| `kakao_send_image` | 채팅방에 이미지 파일 전송 |
| `kakao_download_images` | 채팅 사진 저장 (구형은 계정 캐시에서 복사) |

### 모니터링 도구

| 도구 | 설명 |
|------|------|
| `kakao_start_monitor` | 채팅방 키워드 모니터링 시작 |
| `kakao_stop_monitor` | 모니터링 중지 |
| `kakao_get_monitor_events` | 감지된 키워드 이벤트 조회 |

## 요구사항

- **Windows 10/11** (Win32 API 기반이므로 Windows 전용)
- **카카오톡 PC** 설치 및 로그인 상태
- **Python 3.10** 이상

## 설치

v0.3.0은 구형 Win32와 신형 Qt 카카오톡을 함께 지원합니다. 아래 명령은 GitHub Release의 공통 패키지를 사용합니다.

### uvx

별도 설치 없이 바로 실행:

```bash
uvx --from https://github.com/Kwon-Sungeon/kakaotalk-mcp/releases/download/v0.3.0/kakaotalk_mcp-0.3.0-py3-none-any.whl kakaotalk-mcp
```

### pip

```bash
python -m pip install "https://github.com/Kwon-Sungeon/kakaotalk-mcp/releases/download/v0.3.0/kakaotalk_mcp-0.3.0-py3-none-any.whl"
```

### 소스에서 설치

```bash
git clone https://github.com/Kwon-Sungeon/kakaotalk-mcp.git
cd kakaotalk-mcp
pip install -e .
```

## 설정

### Codex

소스 설치에 사용한 가상 환경의 절대 경로를 등록합니다.

```powershell
codex mcp add kakao -- 'C:\경로\kakaotalk-mcp\.venv\Scripts\python.exe' -m kakao_mcp
```

### Claude Desktop

Claude Desktop 개발자 설정에서 여는 `claude_desktop_config.json`의 `mcpServers`에 추가합니다. 소스 설치 시 `command`에는 가상 환경의 절대 Python 경로를, `args`에는 `["-m", "kakao_mcp"]`를 지정합니다.

**uvx 사용 시:**

```json
{
  "mcpServers": {
    "kakao": {
      "command": "uvx",
      "args": ["--from", "https://github.com/Kwon-Sungeon/kakaotalk-mcp/releases/download/v0.3.0/kakaotalk_mcp-0.3.0-py3-none-any.whl", "kakaotalk-mcp"]
    }
  }
}
```

**pip 설치 후:**

```json
{
  "mcpServers": {
    "kakao": {
      "command": "kakaotalk-mcp"
    }
  }
}
```

**소스에서 직접 실행:**

```json
{
  "mcpServers": {
    "kakao": {
      "command": "python",
      "args": ["C:/경로/kakaotalk-mcp/src/kakao_mcp/server.py"],
      "env": {
        "PYTHONPATH": "C:/경로/kakaotalk-mcp/src"
      }
    }
  }
}
```

### Claude Code

Claude Code 설정에서 MCP 서버 추가:

```bash
claude mcp add kakao -- uvx --from https://github.com/Kwon-Sungeon/kakaotalk-mcp/releases/download/v0.3.0/kakaotalk_mcp-0.3.0-py3-none-any.whl kakaotalk-mcp
```

또는 `.claude/settings.json`에 직접 추가:

```json
{
  "mcpServers": {
    "kakao": {
      "command": "uvx",
      "args": ["--from", "https://github.com/Kwon-Sungeon/kakaotalk-mcp/releases/download/v0.3.0/kakaotalk_mcp-0.3.0-py3-none-any.whl", "kakaotalk-mcp"]
    }
  }
}
```

## 사용 예시

Claude에게 자연어로 요청하면 됩니다:

- "카카오톡 실행 중인지 확인해줘"
- "열린 채팅방 목록 보여줘"
- "홍길동 채팅방 열어줘"
- "홍길동에게 '회의 10분 후에 시작합니다' 보내줘"
- "홍길동에게 @멘션으로 '확인 부탁드립니다' 보내줘"
- "홍길동 채팅방 최근 대화 읽어줘"
- "홍길동 채팅방에서 공유된 링크 추출해줘"
- "이 이미지를 홍길동에게 보내줘: C:\Users\사용자\Pictures\photo.jpg"
- "최근 카카오톡 이미지 다운로드해줘"
- "홍길동, 김철수, 이영희에게 '내일 회의 참석 부탁드립니다' 보내줘"
- "공학자들 채팅방에서 '회의', '점심' 키워드를 모니터링하고 감지되면 답장해줘"
- "모니터링 중지해줘"

## 제한사항 및 주의사항

- **Windows 전용**: Win32 API를 사용하므로 macOS/Linux에서는 동작하지 않습니다.
- Qt 일반 텍스트 전송은 해당 방의 UIA 입력란과 전송 버튼을 사용합니다. 기존 초안이 있거나 본문이 달라지면 중단합니다.
- 이미 열린 방을 `kakao_open_room`으로 지정하면 작업 중인 창을 유지합니다. 방을 화면 앞으로 가져오려면 `focus=true`를 지정합니다. Qt에서 닫힌 방 검색과 사진 열기는 UIA 호출을 먼저 시도하고, 대상 창이 열리지 않을 때 클릭합니다.
- 최근 대화 조회 시 기본 `auto` 모드는 Qt에서 UIA에 노출된 대화를 먼저 읽습니다. 개수를 생략하면 최대 50개를 반환하며, 현재 노출 범위는 로드된 전체 대화보다 짧을 수 있습니다. 개수를 지정하면 최신 위치·개수·발신자·날짜가 확인될 때만 이 경로를 사용하고 부족하면 대화를 복사합니다. 전체 링크 추출도 기본적으로 로드된 대화를 복사합니다.
- 대화 조회, 링크 추출, 모니터링은 호출별로 `read_mode`를 지정할 수 있습니다. `background`는 화면과 입력을 건드리지 않고 노출된 정보만 읽습니다. `complete`는 포커스와 클립보드를 사용해 로드된 대화의 상세 정보를 읽습니다. Qt 조회 결과의 `partial`, `read_method`, `unknown_fields`로 범위를 확인할 수 있습니다. 기존 환경 변수 `KAKAO_QT_READ_MODE=uia`도 계속 지원합니다.
- Qt 모니터링은 기본적으로 백그라운드에서 UIA만 읽습니다. 화면에 노출되지 않은 대화까지 감시해야 하면 `read_mode="complete"`를 사용합니다. 구형은 목록 컨트롤에서 대화 텍스트를 직접 읽고, 지원되지 않으면 복사합니다. 구형 자동 모니터링에서 복사가 필요하면 입력이 3초 이상 멈춘 뒤 조회합니다. 시작 시점에도 입력 중이면 첫 조회까지 `baseline_ready=false`이며, 첫 조회 이후의 새 메시지부터 이벤트를 냅니다.
- Qt 멘션·사진 첨부와 일부 저장 대화상자는 포커스 또는 클립보드를 사용합니다. 구형은 입력란을 직접 채운 뒤 표준 전송 버튼이 하나로 식별되면 해당 버튼을 호출합니다. 버튼을 식별할 수 없으면 방을 활성화해 Enter를 누릅니다. 컨트롤로 직접 입력하거나 포커스를 지정할 수 없으면 기존 붙여넣기 또는 클릭 경로를 사용합니다.
- 상세 대화 복사와 사진 첨부 시 기존 클립보드가 비었거나 일반 텍스트이면, 작업 중 다른 복사가 없었는지 확인하고 복원을 시도합니다. 서식·파일·이미지가 포함된 클립보드는 이 복원 범위에 포함되지 않습니다.
- 사진 저장은 Qt에서 지정한 방의 사진 뷰어를 사용합니다. UIA가 제공하는 사진 중 화면에 표시하거나 스크롤로 이동할 수 있는 항목이 대상입니다. 결과의 `scope`와 `partial`이 범위를 나타냅니다. 구형은 계정 전체의 로컬 이미지 캐시를 사용합니다.
- 방 이름에는 창 제목을 정확하게 입력합니다. 같은 이름의 방이 여러 개면 전송을 중단합니다. 전송 확인이 불확실하면 자동 재전송하지 않습니다.
- Codex와 Claude Desktop의 창 조작은 구형·신형 모두 한 번에 하나씩 실행됩니다. 키워드 모니터와 이벤트 큐는 각 MCP 서버 프로세스에 속하며 폴링 간격은 최소 3초입니다.


## 로드맵

### v0.2.0 — 서버 사이드 자동 응답 (예정)

클라이언트 폴링 방식의 토큰 비효율 문제를 해결하기 위해, MCP 서버 내부에서 채팅 감지 + AI 답장 생성 + 전송을 처리하는 기능을 추가할 예정입니다.

| 도구 | 설명 |
|------|------|
| `kakao_start_auto_reply` | AI 자동 응답 모니터링 시작 (서버 사이드) |
| `kakao_stop_auto_reply` | 자동 응답 중지 |
| `kakao_get_auto_reply_log` | 자동 응답 이력 조회 |

**핵심 개선:**
- 새 메시지 감지는 서버 내부 hash 비교로 처리 (토큰 0)
- AI 답장이 필요할 때만 Claude API를 최소 컨텍스트(최근 10개 메시지)로 호출
- 클라이언트 폴링 대비 **토큰 ~99% 절감**

## 프로젝트 구조

```
kakaotalk-mcp/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── LICENSE
├── requirements.txt
├── src/
│   └── kakao_mcp/
│       ├── __init__.py
│       ├── __main__.py      # python -m kakao_mcp 지원
│       ├── server.py        # MCP 서버 + 13개 도구 정의
│       ├── backend.py       # 실행 중인 UI에 맞는 백엔드 선택
│       ├── qt_controller.py # Qt 작업 직렬화 및 UIA 호출
│       ├── qt_uia.ps1       # Windows UI Automation
│       ├── controller.py    # 구형 Win32 컨트롤 및 모니터
│       ├── parser.py        # 클립보드 텍스트 파싱
│       └── config.py        # 상수 및 설정
└── tests/
    ├── test_parser.py       # 파서 단위 테스트
    └── test_controller.py   # 컨트롤러 mock 테스트
```

## 라이선스

[MIT License](LICENSE)
