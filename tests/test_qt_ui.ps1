# Exercise the shipped mention action with an in-memory chat and no desktop APIs.
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$source = Get-Content 'src/kakao_mcp/qt_uia.ps1' -Raw -Encoding UTF8
$ast = [Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors){throw ($errors -join ', ')}
$switch = $ast.Find({param($node) $node -is [Management.Automation.Language.SwitchStatementAst]},$true)
$clause = $switch.Clauses | Where-Object {$_.Item1.Value -eq 'mention'}
$body = $clause.Item2.Extent.Text
# Reject new dependencies before executing the clause in this harness.
$allowed = 'Room','Front','InputBox','Value','ActivateMention','Children','Where-Object',
           'Select-Object','Click','Key','Start-Sleep','PasteText','SendButton','AssertDraft','AssertRoomReady','Invoke'
foreach($command in $clause.Item2.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)){
 if($command.GetCommandName() -notin $allowed){throw "Unmocked command: $command"}
}
foreach($member in $clause.Item2.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)){
 if($member.Extent.Text -ne '[regex]::Escape($request.mention)'){throw "Unmocked method: $member"}
}
$action = [scriptblock]::Create("switch ('mention') { 'mention' $body }")
function Room($name){if($name -cne 'fixture'){throw 'Wrong room'}; 'fixture'}
function AssertRoomReady($w){if($w -ne 'fixture'){throw 'Wrong room'}}
function Front($w){if($w -cne 'fixture'){throw 'Wrong foreground'}}
function InputBox($w){'input'}
function Value($i){@{Current=@{Value=$script:draft}}}
function ActivateMention($i){$script:popup=$true; $script:draft='@'}
function Children($w){
 if($w -eq 'fixture' -and $script:popup){@{Current=@{ClassName='MentionSuggestionListView'}}}
 if($w.Current.ClassName -eq 'MentionSuggestionListView'){
  foreach($name in $script:candidates){@{Current=@{Name=$name}}}
 }
}
function Click($candidate){$script:clicked=$candidate.Current.Name}
function Key($code){
 if($code -ne 9 -or -not $script:clicked){throw 'Unexpected key'}
 $script:draft=$script:token
 $script:popup=$false
}
function Start-Sleep{}
function PasteText($i,$text){$script:draft+=$text; if($script:changed){$script:draft+='x'}}
function SendButton($w){'fixture button'}
function AssertDraft($i,$expected){if($script:draft -cne $expected){throw 'Chat draft changed'}}
function Invoke($button){
 if($button -cne 'fixture button'){throw 'Wrong button'}
 $script:invokes++
 $script:draft=''
}
function Test-Mention($names,$token,$draft,$preview,$changed,$expectedInvokes,$expectedError){
 $script:candidates=$names; $script:token=$token; $script:draft=$draft
 $script:popup=$false; $script:invokes=0; $script:changed=$changed; $script:clicked=''
 $request=@{room='fixture';mention='Lee';text='sample';preview=$preview}
 $failure=''; $result=$null
 try {$result=& $action} catch {$failure=$_.Exception.Message}
 if($script:invokes -ne $expectedInvokes -or $failure -notlike $expectedError){throw "Unexpected outcome: $failure / invokes=$script:invokes"}
 if(-not $failure -and $preview -and -not $result.ready){throw 'Preview missing'}
 if(-not $failure -and -not $preview -and -not $result.sent){throw 'Send acknowledgement missing'}
}
Test-Mention @('Lee') '@Lee ' '' $true $false 0 ''
Test-Mention @('Lee') '@Lee ' '' $false $false 1 ''
Test-Mention @('Lee','Lee') '' '' $false $false 0 '*exact mention candidate*'
Test-Mention @('LeeAnn') '' '' $false $false 0 '*exact mention candidate*'
Test-Mention @('Lee') '@Other ' '' $false $false 0 '*token*'
Test-Mention @('Lee') '@Lee ' 'existing draft' $false $false 0 '*existing draft*'
Test-Mention @('Lee') '@Lee ' '' $false $true 0 '*draft changed*'
'7 isolated mention scenarios passed'
# A click immediately opens the room viewer and transfers focus to it.
# The production OpenPhoto function must neither click again nor reuse a
# viewer that predates this operation.
$openPhoto = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'OpenPhoto'},$true)
foreach($command in $openPhoto.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)){
 if($command.GetCommandName() -notin 'Windows','Where-Object','Click','Room','Front','WaitFor','TryInvoke','Start-Sleep'){throw "Unmocked command: $command"}
}
. ([scriptblock]::Create($openPhoto.Extent.Text))
$script:viewerOpen=$false
$script:supportsInvoke=$false
function TryInvoke($element){
 if($script:supportsInvoke){$script:viewerOpen=$true; return $true}
 return $false
}
function WaitFor($lookup,$timeout){& $lookup}
function Windows {
 if($script:viewerOpen){@{Current=@{Name='fixture';ClassName='ChatMediaCollectionView'}}}
}
function Click($element, [bool]$double=$false) {
 if($double -or $script:viewerOpen -or $element -ne 'photo'){throw 'Unexpected click'}
 $script:viewerOpen=$true
}
if((OpenPhoto 'photo' 'fixture').Current.Name -ne 'fixture'){throw 'Missing viewer'}
$failure=''
try {OpenPhoto 'photo' 'fixture'} catch {$failure=$_.Exception.Message}
if($failure -notlike '*existing room photo viewer*'){throw 'Unexpected existing viewer handling'}
$script:viewerOpen=$false
$script:supportsInvoke=$true
if((OpenPhoto 'photo' 'fixture').Current.Name -ne 'fixture'){throw 'Missing invoked viewer'}
'3 isolated photo viewer scenarios passed'

# No native window, file picker or button is used in these attachment tests.
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName UIAutomationClient
$definition=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'FileEvidence'},$true)
foreach($command in $definition.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)){
 if($command.GetCommandName() -notin 'Children','Where-Object'){throw "Unmocked command: $command"}
}
. ([scriptblock]::Create($definition.Extent.Text))
function Children($container){return $container}
$label=@{Current=@{Name='fixture.pdf'}}
$button=@{Current=@{Name='전송';ControlType=[System.Windows.Automation.ControlType]::Button;IsEnabled=$true}}
if(-not (FileEvidence @($label,$button) 'fixture.pdf')){throw 'Expected exact matching attachment'}
if(FileEvidence @($label,$button) 'other.pdf'){throw 'Accepted different file'}
if(FileEvidence @($label,$button,$button) 'fixture.pdf'){throw 'Accepted ambiguous buttons'}
'3 isolated file preview scenarios passed'

# The legacy photo path must not press Enter or accept another popup belonging
# to this room. Exercise the shipped guards without loading its input APIs.
$legacyPhoto=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'LegacyImageConfirm'},$true)
foreach($command in $legacyPhoto.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)){
 if($command.GetCommandName() -notin 'FileRoom','WaitFor','OwnedPopup','SendButton','Invoke'){throw "Unexpected input command: $command"}
}
. ([scriptblock]::Create($legacyPhoto.Extent.Text))
function FileRoom {@{Current=@{IsEnabled=$true}}}
function OwnedPopup {@{Current=@{Name='Other settings';ClassName='Window'}}}
function Invoke {throw 'Invoked an unrelated dialog'}
$failure=''
try {LegacyImageConfirm} catch {$failure=$_.Exception.Message}
if($failure -notlike '*Cannot verify photo preview*'){throw "Wrong popup handling: $failure"}
'Legacy unrelated photo popup rejected'

# Verify UTF-8 BOM on both PowerShell files (required for Korean Windows PS 5.1).
foreach($psPath in @('src/kakao_mcp/qt_uia.ps1','tests/test_qt_ui.ps1')){
 $rawBytes=[IO.File]::ReadAllBytes($psPath)
 if($rawBytes.Length -lt 3 -or $rawBytes[0] -ne 0xEF -or $rawBytes[1] -ne 0xBB -or $rawBytes[2] -ne 0xBF){
  throw "Missing UTF-8 BOM on $psPath"
 }
}
'UTF-8 BOM check passed'

# Verify all expected action clauses exist in RunRequest switch.
$clauseNames=@($switch.Clauses | ForEach-Object {$_.Item1.Value})
foreach($expectedAction in @('chats','participants','inspect','close','read','send','mention','file','image','copy','download','open','health','rooms','legacy_ocr','legacy_chat_list_ocr','legacy_open_result','legacy_select_mention','legacy_image_confirm','legacy_photo_ocr_confirm','legacy_send_button')){
 if($expectedAction -notin $clauseNames){throw "Missing switch clause: $expectedAction"}
}
'All action switch clauses verified'

# Isolated Messages tests (Korean 12h, English AM/PM, 24h, English/Korean dates, null filename).
foreach($fnName in @('BubbleParts','Messages','MessageKey','MergeScrollPages','FindNewOutgoingBubble','ChatTab','FindOcrRoomMatch','FindLegacyPopupSendCenter')){
 $fnAst=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $fnName}.GetNewClosure(),$true)
 if(-not $fnAst){throw "Missing function: $fnName"}
 . ([scriptblock]::Create($fnAst.Extent.Text))
}

function New-MockTextPart([string]$content) {
 $part = New-Object PSCustomObject -Property @{
  Current = @{ClassName='TalkObjectText_QML'}
  _content = $content
 }
 $part | Add-Member -MemberType ScriptMethod -Name GetCurrentPattern -Value {
  param($pattern)
  $text = $this._content
  $range = New-Object PSCustomObject -Property @{_t=$text}
  $range | Add-Member -MemberType ScriptMethod -Name GetText -Value {param($limit) $this._t}
  return @{DocumentRange=$range}
 }
 return $part
}

function New-MockBubble([string]$name, [double]$top, $parts) {
 return @{
  Current = @{Name=$name; BoundingRectangle=@{Top=$top}}
  _parts = @($parts)
 }
}

function Children($container) {
 if($container -is [hashtable] -and $container.ContainsKey('_parts')){return $container._parts}
 if($script:mockTabs){return $script:mockTabs}
 return @()
}

$mockItems=@(
 (New-MockBubble '날짜 2026년 11월 12일 목요일' 10 @()),
 (New-MockBubble '김철수, 안녕 https://example.org, 오전 9:03' 20 @((New-MockTextPart '안녕 https://example.org'))),
 (New-MockBubble '내 메시지, 오후 2:15' 30 @((New-MockTextPart '내 메시지'))),
 (New-MockBubble 'Thursday, February 27, 2025' 40 @()),
 (New-MockBubble 'Alice, hello world, 2:15 PM' 50 @((New-MockTextPart 'hello world'))),
 (New-MockBubble 'good night, 12:01 am' 60 @((New-MockTextPart 'good night'))),
 (New-MockBubble 'Date Friday, February 28, 2025' 70 @()),
 (New-MockBubble 'Bob, meeting note, 14:05' 80 @((New-MockTextPart 'meeting note'))),
 (New-MockBubble 'Alice, 파일, 14:06' 90 @(@{Current=@{ClassName='ChatLogContentFile'}}, @{Current=@{ClassName='EmojiText';Name='report.pdf'}})),
 (New-MockBubble 'Alice, 파일, 14:07' 100 @(@{Current=@{ClassName='ChatLogContentFile'}})),
 (New-MockBubble 'Alice, 사진, 14:08' 110 @(@{Current=@{ClassName='ChatLogContentPhoto'}})),
 (New-MockBubble 'Alice, 동영상, 14:09' 120 @(@{Current=@{ClassName='ChatLogContentVideo'}}))
)
$parsed=@(Messages $mockItems)
if($parsed.Count -ne 9){throw "Expected 9 parsed messages, got $($parsed.Count)"}
if($parsed[0].date -cne '2026년 11월 12일 목요일' -or $parsed[0].sender -cne '김철수' -or $parsed[0].time -cne '오전 9:03' -or $parsed[0].outgoing -ne $false -or $parsed[0].urls[0] -cne 'https://example.org'){throw 'Korean incoming message mismatch'}
if($parsed[1].outgoing -ne $true -or $parsed[1].sender -ne $null -or $parsed[1].time -cne '오후 2:15'){throw 'Korean outgoing message mismatch'}
if($parsed[2].date -cne 'Thursday, February 27, 2025' -or $parsed[2].sender -cne 'Alice' -or $parsed[2].time -cne '2:15 PM'){throw 'English PM message mismatch'}
if($parsed[3].outgoing -ne $true -or $parsed[3].time -cne '12:01 am'){throw 'English AM outgoing mismatch'}
if($parsed[4].date -cne 'Friday, February 28, 2025' -or $parsed[4].sender -cne 'Bob' -or $parsed[4].time -cne '14:05'){throw '24h message mismatch'}
if(-not $parsed[5].is_file -or $parsed[5].text -cne '파일: report.pdf'){throw "File message with name mismatch: $($parsed[5].text)"}
if(-not $parsed[6].is_file -or $parsed[6].text -cne '파일'){throw "File message with null filename mismatch: $($parsed[6].text)"}
if(-not $parsed[7].is_photo -or $parsed[7].text -cne '사진'){throw 'Photo message mismatch'}
if(-not $parsed[8].is_video -or $parsed[8].text -cne '동영상'){throw 'Video message mismatch'}
'9 isolated Messages format scenarios passed'

# Isolated MergeScrollPages tests (dedup overlap + date carry-over).
$pOld=@(
 @{outgoing=$false;sender='A';time='10:00';text='first';date='2026년 11월 12일 목요일';is_photo=$false;is_file=$false;is_video=$false},
 @{outgoing=$false;sender='A';time='10:01';text='네';date='2026년 11월 12일 목요일';is_photo=$false;is_file=$false;is_video=$false}
)
$pNew=@(
 @{outgoing=$false;sender='A';time='10:01';text='네';date=$null;is_photo=$false;is_file=$false;is_video=$false},
 @{outgoing=$true;sender=$null;time='10:02';text='네';date=$null;is_photo=$false;is_file=$false;is_video=$false}
)
$merged=@(MergeScrollPages @($pOld,$pNew))
if($merged.Count -ne 3){throw "Expected 3 merged messages, got $($merged.Count)"}
if($merged[2].date -cne '2026년 11월 12일 목요일' -or $merged[2].time -cne '10:02'){throw 'Date carry-over failed'}
'MergeScrollPages deduplication and date carry-over passed'

# Isolated FindNewOutgoingBubble tests.
$b0=@(
 @{outgoing=$false;sender='A';time='10:00';text='hi';is_photo=$false;is_file=$false;is_video=$false},
 @{outgoing=$true;sender=$null;time='10:01';text='dup';is_photo=$false;is_file=$false;is_video=$false}
)
$b1=@(
 $b0[0],
 $b0[1],
 @{outgoing=$true;sender=$null;time='10:02';text='dup';is_photo=$false;is_file=$false;is_video=$false}
)
$found=FindNewOutgoingBubble $b0 $b1 'dup'
if(-not $found -or $found.time -cne '10:02'){throw 'Expected prefix match for new outgoing bubble'}
if(FindNewOutgoingBubble $b0 ($b0 + @(@{outgoing=$false;sender='A';time='10:02';text='dup';is_photo=$false;is_file=$false;is_video=$false})) 'dup'){
 throw 'Rejected incoming bubble failed'
}
$bEmpty=@()
if(-not (FindNewOutgoingBubble $bEmpty @(@{outgoing=$true;sender=$null;time='10:02';text='hello';is_photo=$false;is_file=$false;is_video=$false}) 'hello')){
 throw 'Empty room new outgoing bubble failed'
}
'FindNewOutgoingBubble isolated scenarios passed'

# Isolated ChatTab English & already-selected test.
$script:tabInvokes=0
function Invoke($el){$script:tabInvokes++}
$tabSelected = New-Object PSCustomObject -Property @{Current=@{Name='Chats';ClassName='TabMenuButton_QML'}}
$tabSelected | Add-Member -MemberType ScriptMethod -Name TryGetCurrentPattern -Value {
 param($pat,[ref]$outPat)
 if($pat -eq [System.Windows.Automation.SelectionItemPattern]::Pattern){
  $outPat.Value = @{Current=@{IsSelected=$true}}
  return $true
 }
 return $false
}
$script:mockTabs=@($tabSelected)
ChatTab 'main'
if($script:tabInvokes -ne 0){throw 'Already-selected English Chats tab should not be invoked'}
$tabUnselected = New-Object PSCustomObject -Property @{Current=@{Name='Chats';ClassName='TabMenuButton_QML'}}
$tabUnselected | Add-Member -MemberType ScriptMethod -Name TryGetCurrentPattern -Value {param($pat,[ref]$outPat) return $false}
$script:mockTabs=@($tabUnselected)
ChatTab 'main'
if($script:tabInvokes -ne 1){throw 'Unselected English Chats tab should be invoked once'}
'ChatTab English and selection-skip scenarios passed'

# Isolated FindOcrRoomMatch tests (exact, timestamp strip, badge strip, OCR space split, section preference, ambiguity).
function New-MockOcrLine([string]$text, [double]$y, [array]$wordSpecs) {
 $words=@()
 foreach($ws in $wordSpecs){$words+=@{text=$ws[0];x=[double]$ws[1];y=$y;w=[double]$ws[2];h=16.0}}
 return @{text=$text;words=$words}
}
$ocrSample=@(
 (New-MockOcrLine '친구' 10 @(@('친구',12,30))),
 (New-MockOcrLine '김민수' 40 @(@('김민수',60,45))),
 (New-MockOcrLine '채팅방' 80 @(@('채팅방',12,42))),
 (New-MockOcrLine '김민수 오전 11:10' 110 @(@('김민수',60,45),@('오전',320,24),@('11:10',348,32))),
 (New-MockOcrLine '나 팀채팅방 5 어제' 150 @(@('나',40,12),@('팀채팅방',58,56),@('5',120,10),@('어제',330,24))),
 (New-MockOcrLine '메시지' 190 @(@('메시지',12,42))),
 (New-MockOcrLine '김민수' 220 @(@('김민수',60,45)))
)
$mSelf=FindOcrRoomMatch $ocrSample '김민수'
if(-not $mSelf.match -or $mSelf.match.section -ne 'chats' -or $mSelf.match.cy -ne 118 -or $mSelf.match.cx -ne 82){
 throw "Expected chats-section match with title center (82,118), got: $($mSelf | ConvertTo-Json -Compress)"
}
$mGroup=FindOcrRoomMatch $ocrSample '팀채팅방'
if(-not $mGroup.match -or $mGroup.match.cx -ne 86){throw 'Expected badge and member count strip match'}
$ocrSpaceSplit=@((New-MockOcrLine '김 민수 오후 1:00' 40 @(@('김',60,14),@('민수',78,28),@('오후',320,24),@('1:00',348,32))))
$mSplit=FindOcrRoomMatch $ocrSpaceSplit '김민수'
if(-not $mSplit.match -or $mSplit.match.cx -ne 83){throw 'Expected OCR space-normalized fallback match'}
$ocrAmbig=@(
 (New-MockOcrLine '홍길동' 40 @(@('홍길동',60,45))),
 (New-MockOcrLine '홍길동 어제' 90 @(@('홍길동',60,45),@('어제',330,24)))
)
$mAmbig=FindOcrRoomMatch $ocrAmbig '홍길동'
if(-not $mAmbig.ambiguous){throw 'Expected ambiguous duplicate OCR matches to be rejected'}
$ocrZeroBadge=@((New-MockOcrLine '0 김민수' 40 @(@('0',120,10),@('김민수',135,45))))
$mZero=FindOcrRoomMatch $ocrZeroBadge '김민수'
if(-not $mZero.match -or $mZero.match.cx -ne 158){throw "Expected zero-badge stripped match, got: $($mZero | ConvertTo-Json -Compress)"}
'FindOcrRoomMatch isolated scenarios passed'

# Verify embedded C# KakaoInput / KakaoUia compilation and BinarizeDarkText.
Add-Type -AssemblyName WindowsBase
$initFn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'InitializeInput'},$true)
. ([scriptblock]::Create($initFn.Extent.Text))
InitializeInput
$testBmp=[System.Drawing.Bitmap]::new(2,1)
try {
 $testBmp.SetPixel(0,0,[System.Drawing.Color]::FromArgb(255,25,25,25))
 $testBmp.SetPixel(1,0,[System.Drawing.Color]::FromArgb(255,254,229,0))
 [KakaoInput]::BinarizeDarkText($testBmp)
 if($testBmp.GetPixel(0,0).R -ne 0 -or $testBmp.GetPixel(1,0).R -ne 255){throw 'BinarizeDarkText pixel output mismatch'}
} finally {
 $testBmp.Dispose()
}
'InitializeInput C# and BinarizeDarkText scenarios passed'

# Verify FindLegacyPopupSendCenter ignores header '파일 전송' and locates bottom '1개 전송' button.
$popupLines=@(
 @{text='파일 전송';cx=60;cy=35;words=@(@{text='파일';x=25;y=25;w=30;h=20},@{text='전송';x=60;y=25;w=30;h=20})},
 @{text='sample_report.txt';cx=125;cy=76;words=@(@{text='sample_report.txt';x=60;y=68;w=130;h=16})},
 @{text='1개 전송';cx=150;cy=146;words=@(@{text='1개';x=115;y=138;w=28;h=16},@{text='전송';x=148;y=138;w=32;h=16})}
)
$sendHit=FindLegacyPopupSendCenter $popupLines 184
if(-not $sendHit -or $sendHit.cx -ne 150 -or $sendHit.cy -ne 146){
 throw "Expected popup send button at (150,146), got: $($sendHit | ConvertTo-Json -Compress)"
}
'FindLegacyPopupSendCenter isolated scenarios passed'
