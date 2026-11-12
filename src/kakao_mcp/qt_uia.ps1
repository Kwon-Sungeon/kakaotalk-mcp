$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName WindowsBase
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class KakaoInput {
 [StructLayout(LayoutKind.Sequential)] public struct Point {public int X; public int Y;}
 [DllImport("user32.dll")] public static extern bool GetCursorPos(out Point p);
 public static bool CursorAt(int x,int y){Point p; return GetCursorPos(out p) && p.X == x && p.Y == y;}
 [DllImport("user32.dll")] public static extern uint GetClipboardSequenceNumber();
 [DllImport("user32.dll")] public static extern void keybd_event(byte k,byte s,uint f,UIntPtr e);
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int n);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,int x,int y,int d,UIntPtr e);
}
'@
[void][KakaoInput]::SetProcessDPIAware()
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$script:process = Get-Process KakaoTalkUI -ErrorAction SilentlyContinue | Select-Object -First 1
function Children($element) {
 for($attempt=0;$attempt -lt 3;$attempt++){
  try {return @($element.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))}
  catch {if($attempt -eq 2){throw};Start-Sleep -Milliseconds 150}
 }
}
function Windows {
 @([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object {$_.Current.ProcessId -eq $script:process.Id})
}
function Main {
 $w = @(Windows | Where-Object {$_.Current.ClassName -like 'MainWindow*'})
 if($w.Count -ne 1){throw 'KakaoTalk main window not found'}
 $w[0]
}
function Room($name) {
 $w = @(Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow' -and $_.Current.Name -ceq $name})
 if($w.Count -ne 1){throw "Expected one open chat named '$name'; found $($w.Count)"}
 if($request.hwnd -and $w[0].Current.NativeWindowHandle -ne $request.hwnd){throw "Target room window changed"}
 $w[0]
}
function Front($w) {
 $script:targetHwnd=[IntPtr]$w.Current.NativeWindowHandle
 [void][KakaoInput]::ShowWindow([IntPtr]$w.Current.NativeWindowHandle,9)
 [void][KakaoInput]::SetForegroundWindow([IntPtr]$w.Current.NativeWindowHandle)
 if([KakaoInput]::GetForegroundWindow() -ne $script:targetHwnd){
  [KakaoInput]::keybd_event(18,0,0,[UIntPtr]::Zero);[KakaoInput]::keybd_event(18,0,2,[UIntPtr]::Zero)
  [void][KakaoInput]::SetForegroundWindow([IntPtr]$w.Current.NativeWindowHandle)
 }
 Start-Sleep -Milliseconds 200
 if([KakaoInput]::GetForegroundWindow() -ne $script:targetHwnd){throw 'Could not activate KakaoTalk'}
}
function AssertHit($element,$x,$y) {
 if([KakaoInput]::GetForegroundWindow() -ne $script:targetHwnd){throw 'Target window lost focus'}
 $point=[Windows.Point]::new($x,$y)
 $hit=[System.Windows.Automation.AutomationElement]::FromPoint($point)
 $walker=[System.Windows.Automation.TreeWalker]::RawViewWalker
 while($hit){
  if([System.Windows.Automation.Automation]::Compare($element,$hit)){return}
  $hit=$walker.GetParent($hit)
 }
 throw 'Target element is covered or has moved'
}
function Click($element, [bool]$double=$false) {
 $r=$element.Current.BoundingRectangle
 if($r.IsEmpty -or $element.Current.IsOffscreen){throw 'Target is offscreen'}
 $x=[int]($r.Left+$r.Width/2);$y=[int]($r.Top+$r.Height/2)
 AssertHit $element $x $y
 if(-not [KakaoInput]::SetCursorPos($x,$y)){throw 'Could not position cursor'}
 Start-Sleep -Milliseconds 50
 $count=if($double){2}else{1}
 for($n=0;$n -lt $count;$n++){
  if(-not [KakaoInput]::CursorAt($x,$y)){throw 'Pointer moved before click'}
  AssertHit $element $x $y
  [KakaoInput]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
  [KakaoInput]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
  if($n+1 -lt $count){Start-Sleep -Milliseconds 50}
 }
 Start-Sleep -Milliseconds 250
}
function Value($e){$e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)}
function Invoke($e){$e.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()}
function Key([byte]$key){
 if([KakaoInput]::GetForegroundWindow() -ne $script:targetHwnd){throw 'KakaoTalk lost keyboard focus'}
 [KakaoInput]::keybd_event($key,0,0,[UIntPtr]::Zero); Start-Sleep -Milliseconds 20; [KakaoInput]::keybd_event($key,0,2,[UIntPtr]::Zero)
}
function Ctrl([byte]$key){
 if([KakaoInput]::GetForegroundWindow() -ne $script:targetHwnd){throw 'KakaoTalk lost keyboard focus'}
 [KakaoInput]::keybd_event(17,0,0,[UIntPtr]::Zero)
 try {Key $key} finally {[KakaoInput]::keybd_event(17,0,2,[UIntPtr]::Zero)}
}
function PasteText($element, [string]$text) {
 Add-Type -AssemblyName System.Windows.Forms
 $element.SetFocus()
 if(-not [System.Windows.Automation.Automation]::Compare($element,[System.Windows.Automation.AutomationElement]::FocusedElement)){throw 'Input focus verification failed'}
 [System.Windows.Forms.Clipboard]::SetText($text)
 Ctrl 86
 Start-Sleep -Milliseconds 200
}
function ActivateMention($i) {
   $i.SetFocus()
   if(-not [System.Windows.Automation.Automation]::Compare($i,[System.Windows.Automation.AutomationElement]::FocusedElement)){throw 'Chat input lost focus'}
   [KakaoInput]::keybd_event(16,0,0,[UIntPtr]::Zero)
   try {Key 50} finally {[KakaoInput]::keybd_event(16,0,2,[UIntPtr]::Zero)}
   Start-Sleep -Milliseconds 400
}
function SavePhoto($viewer, [string]$path) {
 $dialog=Children $viewer | Where-Object {$_.Current.ClassName -eq '#32770'} | Select-Object -First 1
 if(-not $dialog){
  Invoke (Children $viewer | Where-Object {$_.Current.Name -eq '저장 Ctrl+S'} | Select-Object -First 1)
  Start-Sleep -Milliseconds 500
  $dialog=Children $viewer | Where-Object {$_.Current.ClassName -eq '#32770'} | Select-Object -First 1
 }
 if(-not $dialog){throw 'Photo save dialog not found'}
 try {
  Front $dialog
  $field=Children $dialog | Where-Object {$_.Current.AutomationId -eq '1001' -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit} | Select-Object -First 1
  $suggested=[IO.Path]::GetExtension((Value $field).Current.Value)
  if($suggested -match '^\.(jpg|jpeg|png|gif|bmp|webp|heic|heif)$'){$path=[IO.Path]::ChangeExtension($path,$suggested)}
  $field.SetFocus(); Ctrl 65; PasteText $field $path
  if((Value $field).Current.Value -ne $path){throw 'Save filename verification failed'}
  Key 9
  Invoke (Children $dialog | Where-Object {$_.Current.AutomationId -eq '1' -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button} | Select-Object -First 1)
  $deadline=[DateTime]::UtcNow.AddSeconds(5)
  while(-not (Test-Path -LiteralPath $path) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100}
  if(-not (Test-Path -LiteralPath $path)){throw 'Photo save did not create the requested file'}
  return $path
 } catch {
  $cancel=Children $dialog | Where-Object {$_.Current.AutomationId -eq '2' -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button} | Select-Object -First 1
  if($cancel){Invoke $cancel}
  throw
 }
}
function InputBox($w){
 $v=@(Children $w | Where-Object {$_.Current.ClassName -like 'TextObjectTextArea*'})
 if($v.Count -ne 1){throw 'Unique chat input not found'}
 $v[0]
}
function SendButton($container) {
 $buttons=@(Children $container | Where-Object {
  $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
  $_.Current.Name -match '^(전송|보내기)$' -and $_.Current.IsEnabled -and -not $_.Current.IsOffscreen
 })
 if($buttons.Count -ne 1){throw 'Unique enabled send button not found'}
 $buttons[0]
}
function AssertDraft($inputElement, [string]$expected) {
 if((Value $inputElement).Current.Value -cne $expected){throw 'Chat draft changed'}
}
function ChatTab($w){
 $tab=Children $w | Where-Object {$_.Current.Name -eq '채팅' -and $_.Current.ClassName -like 'TabMenuButton*'} | Select-Object -First 1
 Invoke $tab
 Start-Sleep -Milliseconds 250
}
function Snapshot($w){
 @(Children $w | Where-Object {$_.Current.ClassName -eq 'CommonChatItemDelegate'} | ForEach-Object {
  $_.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText(-1)
 })
}
function VisibleCenter($window, $element) {
 $bounds=$window.Current.BoundingRectangle
 $item=$element.Current.BoundingRectangle
 if($element.Current.IsOffscreen -or $item.IsEmpty){return $false}
 $y=$item.Top+$item.Height/2
 return $y -gt $bounds.Top+70 -and $y -lt $bounds.Bottom-100
}
function OpenPhoto($photo, $roomName) {
 $viewerFilter={$_.Current.ClassName -like 'ChatMediaCollection*' -and $_.Current.Name -ceq $roomName}
 if(Windows | Where-Object $viewerFilter){throw 'Close the existing room photo viewer first'}
 # Qt opens the photo viewer on the first release. A second click could
 # land on the new viewer instead of the photo in the chat.
 Click $photo
 $deadline=[DateTime]::UtcNow.AddSeconds(3)
 do {
  $viewers=@(Windows | Where-Object $viewerFilter)
  if(-not $viewers.Count){Start-Sleep -Milliseconds 100}
 } while(-not $viewers.Count -and [DateTime]::UtcNow -lt $deadline)
 if($viewers.Count -ne 1){throw 'Unique target photo viewer not found'}
 return $viewers[0]
}
function RevealPhoto($w,$photo) {
 if(VisibleCenter $w $photo){return $true}
 $walker=[System.Windows.Automation.TreeWalker]::RawViewWalker
 $item=$photo
 while($item -and -not [System.Windows.Automation.Automation]::Compare($item,$w)){
  $pattern=$null
  if($item.TryGetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern,[ref]$pattern)){
   $pattern.ScrollIntoView()
   Start-Sleep -Milliseconds 200
   return (VisibleCenter $w $photo)
  }
  $item=$walker.GetParent($item)
 }
 return $false
}
function Messages($w) {
 $messages=@(); $date=$null
 $items=Children $w | Where-Object {$_.Current.ClassName -eq 'CommonChatItemDelegate'} | Sort-Object {$_.Current.BoundingRectangle.Top}
 foreach($element in $items) {
  $sender=$null
  $label=$element.Current.Name
  if($label -match '^날짜 (.+)$'){$date=$Matches[1];continue}
  if($label -notmatch ', ((?:오전|오후) \d{1,2}:\d{2})$'){continue}
  $time=$Matches[1]; $text=$label.Substring(0,$label.Length-$time.Length-2)
  $parts=@(Children $element)
  $bodies=@($parts | Where-Object {$_.Current.ClassName -like 'TalkObjectText*'})
  if($bodies.Count){$text=($bodies | ForEach-Object {$_.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText(-1)}) -join "`n"}
  if($bodies.Count -and $label.EndsWith(', '+$text+', '+$time,[StringComparison]::Ordinal)){
   $sender=$label.Substring(0,$label.Length-$text.Length-$time.Length-4)
  }
  $photo=[bool]($parts | Where-Object {$_.Current.ClassName -like 'ChatLogContentPhoto*'})
  $file=[bool]($parts | Where-Object {$_.Current.ClassName -like 'ChatLogContentFile*'})
  $video=[bool]($parts | Where-Object {$_.Current.ClassName -match 'ChatLogContentVideo'})
  if($file){$filename=$parts|Where-Object {$_.Current.ClassName -like 'EmojiText*'}|Select-Object -First 1; $text='파일: '+$filename.Current.Name}
  if($photo){$text='사진'}
  if($video){$text='동영상'}
  $urls=@([regex]::Matches($text,'https?://[^\s<>"'')\]]+') | ForEach-Object {$_.Value})
  $messages+=@{sender=$sender;time=$time;text=$text;date=$date;is_photo=$photo;is_file=$file;is_video=$video;urls=$urls}
 }
 $messages
}
try {
 if(-not $script:process){throw 'KakaoTalkUI is not running'}
 $result = switch ($request.action) {
  'health' { $w=Main; @{running=$true;hwnd=$w.Current.NativeWindowHandle;pid=$script:process.Id} }
  'rooms' { @{rooms=@(Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow'} | ForEach-Object {@{title=$_.Current.Name;hwnd=$_.Current.NativeWindowHandle}})} }
  'open' {
   $existing=@(Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow' -and $_.Current.Name -ceq $request.room})
   if($existing.Count -eq 1){@{hwnd=$existing[0].Current.NativeWindowHandle}; break}
   if($existing.Count -gt 1){throw 'Multiple open rooms have this name'}
   $w=Main; Front $w; ChatTab $w
   $field=Children $w | Where-Object {$_.Current.ClassName -like 'TextField*'} | Select-Object -First 1
   if(-not $field){Click (Children $w | Where-Object {$_.Current.Name -eq '통합검색 Ctrl+F'} | Select-Object -First 1)}
   $deadline=[DateTime]::UtcNow.AddSeconds(3)
   do {$field=Children $w | Where-Object {$_.Current.ClassName -like 'TextField*'} | Select-Object -First 1; if(-not $field){Start-Sleep -Milliseconds 100}} while(-not $field -and [DateTime]::UtcNow -lt $deadline)
   if(-not $field){throw 'Search input not found'}
   (Value $field).SetValue($request.room); Start-Sleep -Milliseconds 600
   $namePattern='^(?:(?:팀채팅|그룹채팅|나와의 채팅), )?'+[regex]::Escape($request.room)+', '
   $matches=@(Children $w | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::ListItem -and ($_.Current.Name -ceq $request.room -or $_.Current.Name -cmatch $namePattern)})
   if($matches.Count -ne 1){(Value $field).SetValue(''); throw "Expected one exact search result; found $($matches.Count)"}
   Click $matches[0] $true
   (Value $field).SetValue('')
   $opened=Room $request.room
   @{hwnd=$opened.Current.NativeWindowHandle}
  }
  'image' {
   $w=Room $request.room; Front $w; $i=InputBox $w
   AssertDraft $i ''
   if(Children $w | Where-Object {$_.Current.ClassName -like 'ClipboardImageWindow*'}){throw 'An image preview is already open'}
   $i.SetFocus()
   if(-not [System.Windows.Automation.Automation]::Compare($i,[System.Windows.Automation.AutomationElement]::FocusedElement)){throw 'Chat input lost focus'}
   if([KakaoInput]::GetClipboardSequenceNumber() -ne $request.sequence){throw 'Clipboard image changed'}
   Ctrl 86
   Start-Sleep -Milliseconds 600
   $dialogs=@(Children $w | Where-Object {$_.Current.ClassName -like 'ClipboardImageWindow*'})
   if($dialogs.Count -ne 1){throw 'Unique image preview not found in target room'}
   $button=SendButton $dialogs[0]
   Invoke $button; Start-Sleep -Milliseconds 500
   if(Children $w | Where-Object {$_.Current.ClassName -like 'ClipboardImageWindow*'}){throw 'Image send outcome uncertain; do not retry automatically'}
   @{sent=$true}
  }
  'download' {
   $w=Room $request.room; Front $w
   $photos=@(Children $w | Where-Object {$_.Current.ClassName -like 'ChatLogContentPhoto*'} | Sort-Object {$_.Current.BoundingRectangle.Top})
   $scope='visible_room_photos'
   $failure=$null
   $images=@()
   try {
   for($index=$photos.Count-1;$index -ge 0 -and $images.Count -lt $request.limit;$index--){
    Front $w
    if(-not (VisibleCenter $w $photos[$index])){
     if(-not (RevealPhoto $w $photos[$index])){continue}
     $scope='loaded_room_photos'
    }
    $viewer=OpenPhoto $photos[$index] $request.room
    try {
     $path=Join-Path $request.output (([Guid]::NewGuid().ToString('N'))+'.png')
     $path=SavePhoto $viewer $path
     $images+=@{destination=$path;size=(Get-Item -LiteralPath $path).Length}
    } finally {$viewer.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()}
   }
   } catch {$failure=$_.Exception.Message}
   $saved=@{success=($null -eq $failure);images=$images;scope=$scope;partial=$true;message="Saved $($images.Count) room photo(s)"}
   if($failure){$saved.error=$failure}
   $saved
  }
  'mention' {
   $w=Room $request.room; Front $w; $i=InputBox $w; $v=Value $i
   if($v.Current.Value){throw 'Chat has an existing draft'}
   ActivateMention $i
   $list=Children $w | Where-Object {$_.Current.ClassName -like 'MentionSuggestionList*'} | Select-Object -First 1
   $candidates=@(if($list){Children $list | Where-Object {$_.Current.Name -ceq $request.mention -or $_.Current.Name -cmatch ('^(?:나|방장|부방장), '+[regex]::Escape($request.mention)+'$')}})
   if($candidates.Count -ne 1){throw "Expected one exact mention candidate; found $($candidates.Count)"}
   Click $candidates[0]
   Key 9
   Start-Sleep -Milliseconds 250
   $w=Room $request.room; $i=InputBox $w; $v=Value $i
   $selected=(Value (InputBox $w)).Current.Value
   $popup=Children $w | Where-Object {$_.Current.ClassName -like 'MentionSuggestionList*'}
   $prefix='@'+$request.mention+' '
   if($popup -or $selected -cne $prefix){throw 'Selected mention token could not be verified'}
   PasteText $i $request.text
   if(Children $w | Where-Object {$_.Current.ClassName -like 'ClipboardImageWindow*'}){throw 'Image preview is open in target room'}
   $button=SendButton $w
   AssertDraft $i ($prefix+$request.text)
   if($request.preview){@{ready=$true};break}
   Invoke $button; Start-Sleep -Milliseconds 500
   if((Value (InputBox $w)).Current.Value){throw 'Mention send outcome uncertain; do not retry automatically'}
   @{sent=$true}
  }
  'read' { $w=Room $request.room; @{room_name=$w.Current.Name;member_count=$null;messages=@(Messages $w);dates=@();partial=$true;read_method='uia';unknown_fields=@('member_count','sender')} }
  'copy' {
   $w=Room $request.room; Front $w
   $items=@(Children $w | Where-Object {$_.Current.ClassName -eq 'CommonChatItemDelegate'})
   if(-not $items.Count){@{raw_text=''};break}
   $message=$items | Where-Object {(VisibleCenter $w $_) -and $_.Current.Name -notmatch '^날짜 '} | Select-Object -Last 1
   if(-not $message){throw 'No visible message to copy'}
   Click $message
   Add-Type -AssemblyName System.Windows.Forms
   [System.Windows.Forms.Clipboard]::Clear()
   Ctrl 65; Start-Sleep -Milliseconds 100
   Ctrl 67; Start-Sleep -Milliseconds 200
   $text=[System.Windows.Forms.Clipboard]::GetText()
   if(-not $text){throw 'Message copy returned an empty clipboard'}
   @{raw_text=$text}
  }
  'send' {
   $w=Room $request.room; $i=InputBox $w; $v=Value $i
   $before=@(Snapshot $w | Where-Object {$_.StartsWith($request.text+', ')}).Count
   if($v.Current.Value){throw 'Chat has an existing draft'}
   $v.SetValue($request.text)
   if($v.Current.Value -cne $request.text){throw 'Input verification failed'}
   if(Children $w | Where-Object {$_.Current.ClassName -like 'ClipboardImageWindow*'}){throw 'Image preview is open in target room'}
   $button=SendButton $w
   AssertDraft $i $request.text
   Invoke $button; Start-Sleep -Milliseconds 500
   $fresh=Value (InputBox $w)
   if($fresh.Current.Value){throw 'Send outcome uncertain; do not retry automatically'}
   $after=@(Snapshot $w | Where-Object {$_.StartsWith($request.text+', ')}).Count
   if($after -le $before){throw 'New message bubble not confirmed; do not retry automatically'}
   @{sent=$true}
  }
  default {throw "Unknown action $($request.action)"}
 }
 @{success=$true;data=$result} | ConvertTo-Json -Depth 14 -Compress
} catch { @{success=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress }
