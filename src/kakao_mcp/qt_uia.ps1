$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName WindowsBase
function InitializeInput {
 if('KakaoInput' -as [type]){return}
 Add-Type @'
using System;
using System.Runtime.InteropServices;
public class KakaoInput {
 [StructLayout(LayoutKind.Sequential)] public struct Point {public int X; public int Y;}
 [DllImport("user32.dll")] public static extern bool GetCursorPos(out Point p);
 public static Point Cursor(){Point p; if(!GetCursorPos(out p)) throw new InvalidOperationException("Cursor unavailable"); return p;}
 [StructLayout(LayoutKind.Sequential)] private struct LastInput { public uint Size; public uint Tick; }
 [DllImport("user32.dll")] private static extern bool GetLastInputInfo(ref LastInput info);
 public static uint InputTick(){var info=new LastInput{Size=8}; if(!GetLastInputInfo(ref info)) throw new InvalidOperationException("Input time unavailable"); return info.Tick;}
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern IntPtr SendMessageTimeoutW(IntPtr hwnd,uint message,IntPtr w,IntPtr l,uint flags,uint timeout,out IntPtr result);
 public static bool ReplaceFilename(IntPtr hwnd,string text){
  IntPtr result;
  if(hwnd==IntPtr.Zero || SendMessageTimeoutW(hwnd,0xB1,IntPtr.Zero,new IntPtr(-1),2,500,out result)==IntPtr.Zero) return false;
  IntPtr value=Marshal.StringToHGlobalUni(text);
  try { return SendMessageTimeoutW(hwnd,0xC2,new IntPtr(1),value,2,500,out result)!=IntPtr.Zero; }
  finally { Marshal.FreeHGlobal(value); }
 }
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] private static extern IntPtr SendMessageTimeoutW(IntPtr h,uint m,IntPtr w,System.Text.StringBuilder l,uint f,uint t,out IntPtr result);
 public static bool FilenameEquals(IntPtr h,string path){
  var value=new System.Text.StringBuilder(path.Length+2); IntPtr result;
  return SendMessageTimeoutW(h,0x0D,new IntPtr(value.Capacity),value,2,500,out result)!=IntPtr.Zero && value.ToString()==path;
 }
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern bool PostMessageW(IntPtr h,uint m,IntPtr w,IntPtr l);
 public static bool PasteFile(IntPtr room,IntPtr edit,uint sequence){
  IntPtr result;
  return IsChild(room,edit) && FilenameEquals(edit,"") && GetClipboardSequenceNumber()==sequence &&
         SendMessageTimeoutW(edit,0x302,IntPtr.Zero,IntPtr.Zero,2,1500,out result)!=IntPtr.Zero;
 }
 public static bool CursorAt(int x,int y){Point p; return GetCursorPos(out p) && p.X == x && p.Y == y;}
 [DllImport("user32.dll")] public static extern uint GetClipboardSequenceNumber();
 [DllImport("user32.dll")] private static extern IntPtr GetClipboardOwner();
 [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
 public static uint ClipboardOwnerPid(){uint pid; GetWindowThreadProcessId(GetClipboardOwner(),out pid); return pid;}
 [DllImport("user32.dll")] public static extern void keybd_event(byte k,byte s,uint f,UIntPtr e);
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int n);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h,uint cmd);
 [DllImport("user32.dll")] public static extern bool IsChild(IntPtr h,IntPtr child);
 [DllImport("user32.dll")] public static extern IntPtr GetLastActivePopup(IntPtr h);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,int x,int y,int d,UIntPtr e);
}
'@
 [void][KakaoInput]::SetProcessDPIAware()
}
function Children($element) {
 for($attempt=0;$attempt -lt 3;$attempt++){
  try {return @($element.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))}
  catch {if($attempt -eq 2){throw};Start-Sleep -Milliseconds 150}
 }
}
function Windows {
 $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,[int]$request.process_id)
 @([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$condition))
}
function Main {
 $w = @(Windows | Where-Object {$_.Current.ClassName -like 'MainWindow*'})
 if($w.Count -ne 1){throw 'KakaoTalk main window not found'}
 $w[0]
}
function Room($name) {
 $w = @(Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow' -and $_.Current.Name -ceq $name})
 if($w.Count -ne 1){throw "Expected one open chat named '$name'; found $($w.Count)"}
 $handle=$w[0].Current.NativeWindowHandle
 if(($request.hwnd -and $handle -ne $request.hwnd) -or ($script:roomHwnd -and $handle -ne $script:roomHwnd)){throw 'Target room window changed'}
 $script:roomHwnd=$handle
 $w[0]
}
function Front($w) {
 $script:targetHwnd=[IntPtr]$w.Current.NativeWindowHandle
 if([KakaoInput]::GetForegroundWindow() -eq $script:targetHwnd){return}
 if([KakaoInput]::IsIconic($script:targetHwnd)){[void][KakaoInput]::ShowWindow($script:targetHwnd,9)}
 [void][KakaoInput]::SetForegroundWindow([IntPtr]$w.Current.NativeWindowHandle)
 if([KakaoInput]::GetForegroundWindow() -ne $script:targetHwnd){
  [KakaoInput]::keybd_event(18,0,0,[UIntPtr]::Zero);[KakaoInput]::keybd_event(18,0,2,[UIntPtr]::Zero)
  $script:lastInput=[KakaoInput]::InputTick()
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
 $previous=[KakaoInput]::Cursor()
 try {
  if(-not [KakaoInput]::SetCursorPos($x,$y)){throw 'Could not position cursor'}
  Start-Sleep -Milliseconds 50
  $count=if($double){2}else{1}
  for($n=0;$n -lt $count;$n++){
   if(-not [KakaoInput]::CursorAt($x,$y)){throw 'Pointer moved before click'}
   AssertHit $element $x $y
   [KakaoInput]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
   [KakaoInput]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
   $script:lastInput=[KakaoInput]::InputTick()
   if($n+1 -lt $count){Start-Sleep -Milliseconds 50}
  }
 } finally {
  if([KakaoInput]::CursorAt($x,$y)){[void][KakaoInput]::SetCursorPos($previous.X,$previous.Y)}
 }
 Start-Sleep -Milliseconds 250
}
function Value($e){$e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)}
function Invoke($e){$e.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()}
function TryInvoke($element) {
 $pattern=$null
 if($null -eq $element -or -not $element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)){return $false}
 try {$pattern.Invoke()}
 catch [System.InvalidOperationException] {return $false}
 catch [System.Runtime.InteropServices.COMException] {return $false}
 return $true
}
function WaitFor($lookup, [int]$milliseconds) {
 $deadline=[DateTime]::UtcNow.AddMilliseconds($milliseconds)
 do {
  $found=@(& $lookup)
  if($found.Count){return $found}
  Start-Sleep -Milliseconds 80
 } while([DateTime]::UtcNow -lt $deadline)
}
function Key([byte]$key){
 if([KakaoInput]::GetForegroundWindow() -ne $script:targetHwnd){throw 'KakaoTalk lost keyboard focus'}
 [KakaoInput]::keybd_event($key,0,0,[UIntPtr]::Zero); Start-Sleep -Milliseconds 20; [KakaoInput]::keybd_event($key,0,2,[UIntPtr]::Zero)
 $script:lastInput=[KakaoInput]::InputTick()
}
function Ctrl([byte]$key){
 if([KakaoInput]::GetForegroundWindow() -ne $script:targetHwnd){throw 'KakaoTalk lost keyboard focus'}
 [KakaoInput]::keybd_event(17,0,0,[UIntPtr]::Zero)
 try {Key $key} finally {[KakaoInput]::keybd_event(17,0,2,[UIntPtr]::Zero); $script:lastInput=[KakaoInput]::InputTick()}
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
  $field=Children $dialog | Where-Object {$_.Current.AutomationId -eq '1001' -and $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit} | Select-Object -First 1
  $suggested=[IO.Path]::GetExtension((Value $field).Current.Value)
  if($suggested -match '^\.(jpg|jpeg|png|gif|bmp|webp|heic|heif)$'){$path=[IO.Path]::ChangeExtension($path,$suggested)}
  $editHwnd=[IntPtr]$field.Current.NativeWindowHandle
  $direct=$field.Current.ClassName -eq 'Edit' -and [KakaoInput]::ReplaceFilename($editHwnd,$path)
  if(-not $direct -or (Value $field).Current.Value -cne $path){
   Front $dialog
   $field.SetFocus(); Ctrl 65; PasteText $field $path
   Key 9
  }
  if((Value $field).Current.Value -ne $path){throw 'Save filename verification failed'}
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
  $_.Current.Name.Replace('&','') -match '^(전송|보내기|Send)(\([A-Za-z]\))?$' -and $_.Current.IsEnabled -and -not $_.Current.IsOffscreen
 })
 if($buttons.Count -ne 1){throw 'Unique enabled send button not found'}
 $buttons[0]
}
function AssertDraft($inputElement, [string]$expected) {
 if((Value $inputElement).Current.Value -cne $expected){throw 'Chat draft changed'}
}
function FileRoom {
 $w=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$request.hwnd)
 if($w.Current.ProcessId -ne $request.process_id -or $w.Current.Name -cne $request.room){throw 'File target room changed'}
 if($request.qt){return (Room $request.room)}
 if($w.Current.ClassName -ne 'EVA_Window_Dblclk'){throw 'Unexpected legacy chat window'}
 $script:roomHwnd=$request.hwnd
 return $w
}
function OwnedPopup {
 foreach($h in @([KakaoInput]::GetLastActivePopup([IntPtr]$request.hwnd),[KakaoInput]::GetForegroundWindow())){
  if($h -eq [IntPtr]::Zero -or $h -eq [IntPtr]$request.hwnd -or -not [KakaoInput]::IsWindowVisible($h)){continue}
  $owner=$h
  for($level=0;$level -lt 5;$level++){
   $owner=[KakaoInput]::GetWindow($owner,4)
   if($owner -eq [IntPtr]::Zero){break}
   if($owner -eq [IntPtr]$request.hwnd){
    $el=[System.Windows.Automation.AutomationElement]::FromHandle($h)
    if($el.Current.ProcessId -eq $request.process_id){return $el}
    break
   }
  }
 }
}
function FilePick($w) {
 if((OwnedPopup) -or -not $w.Current.IsEnabled){throw 'Close the existing room dialog before attaching'}
 if($request.qt){
  AssertDraft (InputBox $w) ''
  if(-not $request.sequence){throw 'File clipboard sequence required'}
  Front $w
  $inputElement=InputBox (FileRoom)
  $inputElement.SetFocus()
  Add-Type -AssemblyName System.Windows.Forms
  $paths=[System.Windows.Forms.Clipboard]::GetFileDropList()
  if($paths.Count -ne 1 -or $paths[0] -cne $request.path){throw 'File clipboard changed'}
  AssertDraft $inputElement ''
  if(-not [System.Windows.Automation.Automation]::Compare($inputElement,[System.Windows.Automation.AutomationElement]::FocusedElement) -or
     [KakaoInput]::GetClipboardSequenceNumber() -ne $request.sequence){throw 'File input or clipboard changed'}
  $script:dispatched=$true
  Ctrl 86
  return
 }else{
  Front $w
  if((OwnedPopup) -or -not (FileRoom).Current.IsEnabled){throw 'Legacy room has an open dialog'}
  if($request.edit_hwnd){
   $edit=[IntPtr]$request.edit_hwnd
   if(-not [KakaoInput]::IsChild([IntPtr]$request.hwnd,$edit)){throw 'Legacy input changed'}
   $field=[System.Windows.Automation.AutomationElement]::FromHandle($edit)
   if($field.Current.ClassName -ine 'RICHEDIT50W' -or $field.Current.ProcessId -ne $request.process_id){throw 'Legacy input changed'}
   $field.SetFocus()
   Add-Type -AssemblyName System.Windows.Forms
   $paths=[System.Windows.Forms.Clipboard]::GetFileDropList()
   if($paths.Count -ne 1 -or $paths[0] -cne $request.path -or
      -not [KakaoInput]::FilenameEquals($edit,'') -or
      [KakaoInput]::GetClipboardSequenceNumber() -ne $request.sequence){throw 'Legacy draft or file clipboard changed'}
   $null=FileRoom
   if(-not [KakaoInput]::IsChild([IntPtr]$request.hwnd,$edit)){throw 'Legacy input changed'}
   $script:dispatched=$true
   if(-not [KakaoInput]::PasteFile([IntPtr]$request.hwnd,$edit,$request.sequence)){throw 'Could not submit file paste'}
   return
  }
  Ctrl 84
 }
 SelectFilePath
}
function SelectFilePath {
 $picker=WaitFor {OwnedPopup | Where-Object {$_.Current.ClassName -eq '#32770'}} 3500
 if(-not $picker){throw 'Room-owned Windows file picker did not open; no file selected'}
 $controls=@(Children $picker)
 $combos=@($controls | Where-Object {$_.Current.AutomationId -in '1148','FileNameControlHost'})
 $edits=@(if($combos.Count -eq 1){if($combos[0].Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit){$combos[0]}else{Children $combos[0] | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit}}})
 if($edits.Count -eq 0){$edits=@($controls | Where-Object {$_.Current.AutomationId -in '1001','1148' -and $_.Current.ClassName -eq 'Edit'})}
 $ok=@($controls | Where-Object {$_.Current.AutomationId -eq '1' -and ($_.Current.ClassName -eq 'Button' -or $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button) -and $_.Current.Name.Replace('&','') -match '^(열기|Open)(\([A-Za-z]\))?$'})
 if($ok.Count -ne 1 -or $edits.Count -ne 1){throw 'Cannot verify the file name field and Open button'}
 $editHwnd=[IntPtr]$edits[0].Current.NativeWindowHandle
 if($edits[0].Current.ClassName -eq 'Edit' -and [KakaoInput]::ReplaceFilename($editHwnd,$request.path)){
  $matchesPath=[KakaoInput]::FilenameEquals($editHwnd,$request.path)
 }else{
  $val=Value $edits[0]
  $val.SetValue($request.path)
  $matchesPath=$val.Current.Value -ceq $request.path
 }
 if(-not $matchesPath -or (OwnedPopup).Current.NativeWindowHandle -ne $picker.Current.NativeWindowHandle){throw 'File picker or selected path changed'}
 $null=FileRoom
 if($ok[0].Current.ClassName -eq 'Button' -and $ok[0].Current.NativeWindowHandle){
  $script:dispatched=$true
  if(-not [KakaoInput]::PostMessageW([IntPtr]$ok[0].Current.NativeWindowHandle,0xF5,[IntPtr]::Zero,[IntPtr]::Zero)){throw 'Could not submit selected file'}
 }else{$script:dispatched=$true; Invoke $ok[0]}
}
function FileEvidence($container, $name) {
 $els=@(Children $container)
 $exact=@($els | Where-Object {$_.Current.Name -ceq $name})
 if(-not $exact.Count){return}
 $send=@($els | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.IsEnabled -and $_.Current.Name.Replace('&','') -match '^(전송|보내기|Send)(\([A-Za-z]\))?$'})
 if($send.Count -eq 1){return $send[0]}
}
function FilePreview {
 $popup=OwnedPopup
 if($popup){return $popup}
 $w=FileRoom
 if($request.qt){
  $panels=@(Children $w | Where-Object {$_.Current.ClassName -match '(File.*(Window|Dialog)|(Window|Dialog).*File)' -and -not $_.Current.IsOffscreen})
  if($panels.Count -eq 1){return $panels[0]}
 }
}
function SendFile {
 $w=FileRoom
 if(FilePreview){throw 'Close the existing file preview before attaching'}
 $name=[IO.Path]::GetFileName($request.path)
 $before=0
 $bubbles={
  $w=FileRoom
  @(Children $w | Where-Object {$_.Current.ClassName -like 'ChatLogContentFile*'} | Where-Object {
    @(Children $_ | Where-Object {$_.Current.Name -ceq $name}).Count -gt 0
  }).Count
 }
 if($request.qt){$before=& $bubbles}
 FilePick $w
 $clicked=$false
 $deadline=[DateTime]::UtcNow.AddSeconds(6)
 do {
  if($request.qt -and (& $bubbles) -gt $before){return @{sent=$true;confirmation='room_bubble'}}
  $preview=FilePreview
  if($preview -and -not $clicked){
   $button=FileEvidence $preview $name
   if($button){
    $null=FileRoom
    Invoke $button
    $clicked=$true
   }
  }elseif(-not $request.qt -and $clicked -and -not $preview -and (FileRoom).Current.IsEnabled){
   return @{sent=$true;confirmation='verified_file_preview_closed'}
  }
  Start-Sleep -Milliseconds 120
 }while([DateTime]::UtcNow -lt $deadline)
 throw 'File attachment started, but sending could not be confirmed. Inspect the room; do not retry automatically.'
}
function AssertRoomReady($w) {
 if(-not $w.Current.IsEnabled -or (OwnedPopup)){throw 'Room has an open dialog'}
 $modal=Children $w | Where-Object {
   -not $_.Current.IsOffscreen -and $_.Current.ClassName -match '(ClipboardImageWindow|File.*(Window|Dialog)|Confirm.*(Window|Dialog))'
 } | Select-Object -First 1
 if($modal){throw 'Room has an open preview or confirmation dialog'}
}
function ExactItem($list, $name) {
 $items=@(Children $list | Where-Object {
  $_.Current.Name -ceq $name -and -not $_.Current.IsOffscreen -and $_.Current.IsEnabled -and
  $_.Current.ControlType -in [System.Windows.Automation.ControlType]::ListItem,[System.Windows.Automation.ControlType]::DataItem
 })
 if($items.Count -gt 1){throw 'Ambiguous exact list items'}
 if($items.Count -eq 1){return $items[0]}
}
function LegacyOpenResult {
 $main=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$request.hwnd)
 $list=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$request.list_hwnd)
 if($main.Current.ProcessId -ne $request.process_id -or $list.Current.ProcessId -ne $request.process_id -or
    -not [KakaoInput]::IsChild([IntPtr]$request.hwnd,[IntPtr]$request.list_hwnd)){throw 'Search list owner changed'}
 $item=WaitFor {ExactItem $list $request.room} 2200
 if(-not $item){throw 'Cannot verify exact room in legacy search results; open the room manually'}
 if(-not [KakaoInput]::FilenameEquals([IntPtr]$request.edit_hwnd,$request.room)){throw 'Search text changed'}
 $current=ExactItem $list $request.room
 if(-not $current -or -not [System.Windows.Automation.Automation]::Compare($item,$current)){throw 'Room search results changed'}
 $pattern=$null
 if($current.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)){$pattern.Invoke()}
 else{Front $main; Click $current $true}
 @{opened=$true}
}
function LegacyMention {
 $w=FileRoom
 $lists=@(Children $w | Where-Object {$_.Current.ClassName -match 'mention' -and
           $_.Current.ControlType -in [System.Windows.Automation.ControlType]::List,[System.Windows.Automation.ControlType]::Pane})
 $popup=OwnedPopup
 if($popup -and ($popup.Current.ClassName -match 'mention' -or $popup.Current.Name -in '멘션','Mention')){$lists=@($popup)}
 if($lists.Count -ne 1){throw 'Cannot verify legacy mention popup; draft preserved'}
 $candidate=ExactItem $lists[0] $request.mention
 if(-not $candidate){throw 'Cannot verify unique exact legacy mention candidate; draft preserved'}
 $null=FileRoom
 Invoke $candidate
 @{selected=$true}
}
function LegacyImageConfirm {
 $w=FileRoom
 $preview=WaitFor {OwnedPopup} 2500
 if(-not $preview -or ($preview.Current.ClassName -notmatch '(ClipboardImage|ImageSend|PhotoSend)' -and
    $preview.Current.Name -notin '사진 전송','이미지 전송','사진 보내기','Send photo','Send image')){throw 'Cannot verify photo preview in the exact room'}
 $button=SendButton $preview
 $null=FileRoom
 if(-not [System.Windows.Automation.Automation]::Compare($preview,(OwnedPopup))){throw 'Photo preview changed'}
 Invoke $button
 if(-not (WaitFor {if(-not (OwnedPopup) -and (FileRoom).Current.IsEnabled){$true}} 2200)){throw 'Photo send unconfirmed'}
 @{sent=$true;confirmation='verified_photo_preview_closed'}
}
function ChatTab($w){
 $tab=Children $w | Where-Object {$_.Current.Name -eq '채팅' -and $_.Current.ClassName -like 'TabMenuButton*'} | Select-Object -First 1
 Invoke $tab
 Start-Sleep -Milliseconds 250
}
function Snapshot($w){
 @(ChatItems $w | ForEach-Object {
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
 $lookup={Windows | Where-Object $viewerFilter}
 $viewers=@()
 if(TryInvoke $photo){$viewers=@(WaitFor $lookup 1000)}
 if(-not $viewers.Count){
  Front (Room $roomName)
  if(-not (Windows | Where-Object $viewerFilter)){Click $photo}
  $viewers=@(WaitFor $lookup 3000)
 }
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
function ChatItems($w) {
 $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,'CommonChatItemDelegate')
 $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)
}
function Messages($items) {
 $messages=@(); $date=$null
 foreach($element in ($items | Sort-Object {$_.Current.BoundingRectangle.Top})) {
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
function RunRequest($request) {
 $script:dispatched=$false
 $script:roomHwnd=$null
 $script:targetHwnd=$null
 $previousWindow=[IntPtr]::Zero
 try {
 if($request.action -in 'send','copy','image','file','mention','download','open','legacy_open_result','legacy_select_mention','legacy_image_confirm','legacy_send_button'){
  InitializeInput
  $previousWindow=[KakaoInput]::GetForegroundWindow()
  $script:lastInput=[KakaoInput]::InputTick()
 }
 $result = switch ($request.action) {
  'legacy_send_button' {
   $w=FileRoom
   if((OwnedPopup) -or -not $w.Current.IsEnabled){throw 'Room has an open dialog'}
   AssertRoomReady $w
   $button=SendButton $w
   $draft=$request.text.Replace("`r`n","`n").Replace("`n","`r`n")
   if(-not [KakaoInput]::FilenameEquals([IntPtr]$request.edit_hwnd,$draft)){throw 'Draft changed'}
   $null=FileRoom
   $script:dispatched=$true
   Invoke $button
   @{submitted=$true}
  }
  'legacy_open_result' {LegacyOpenResult}
  'legacy_select_mention' {LegacyMention}
  'legacy_image_confirm' {$script:dispatched=$true; LegacyImageConfirm}
  'file' {SendFile}
  'health' { $w=Main; @{running=$true;hwnd=$w.Current.NativeWindowHandle;pid=$request.process_id} }
  'rooms' { @{rooms=@(Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow'} | ForEach-Object {@{title=$_.Current.Name;hwnd=$_.Current.NativeWindowHandle}})} }
  'open' {
   $existing=@(Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow' -and $_.Current.Name -ceq $request.room})
   if($existing.Count -eq 1){@{hwnd=$existing[0].Current.NativeWindowHandle}; break}
   if($existing.Count -gt 1){throw 'Multiple open rooms have this name'}
   $w=Main; ChatTab $w
   $field=Children $w | Where-Object {$_.Current.ClassName -like 'TextField*'} | Select-Object -First 1
   if(-not $field){
    $button=Children $w | Where-Object {$_.Current.Name -eq '통합검색 Ctrl+F'} | Select-Object -First 1
    $lookup={Children $w | Where-Object {$_.Current.ClassName -like 'TextField*'} | Select-Object -First 1}
    if(TryInvoke $button){$field=WaitFor $lookup 1000}
    if(-not $field){
     Front $w
     if(-not (& $lookup)){Click $button}
     $field=WaitFor $lookup 3000
    }
   }
   if(-not $field){throw 'Search input not found'}
   $searchValue=Value $field
   $previousQuery=$searchValue.Current.Value
   try {
    (Value $field).SetValue($request.room)
    $namePattern='^(?:(?:팀채팅|그룹채팅|나와의 채팅), )?'+[regex]::Escape($request.room)+', '
    $matches=@(WaitFor {Children $w | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::ListItem -and ($_.Current.Name -ceq $request.room -or $_.Current.Name -cmatch $namePattern)}} 3000)
    if($matches.Count -ne 1){throw "Expected one exact search result; found $($matches.Count)"}
    $lookup={Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow' -and $_.Current.Name -ceq $request.room}}
    $opened=$null
    if(TryInvoke $matches[0]){$opened=WaitFor $lookup 1000}
    if(-not $opened){
     Front $w
     if(-not (& $lookup)){Click $matches[0] $true}
     $opened=WaitFor $lookup 3000
    }
   } finally {
    if($searchValue.Current.Value -ceq $request.room){$searchValue.SetValue($previousQuery)}
   }
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
   $null=Room $request.room
   $script:dispatched=$true
   Ctrl 86
   Start-Sleep -Milliseconds 600
   $dialogs=@(Children $w | Where-Object {$_.Current.ClassName -like 'ClipboardImageWindow*'})
   if($dialogs.Count -ne 1){throw 'Unique image preview not found in target room'}
   $button=SendButton $dialogs[0]
   $null=Room $request.room
   $script:dispatched=$true
   Invoke $button; Start-Sleep -Milliseconds 500
   if(Children $w | Where-Object {$_.Current.ClassName -like 'ClipboardImageWindow*'}){throw 'Image send outcome uncertain; do not retry automatically'}
   @{sent=$true}
  }
  'download' {
   $w=Room $request.room
   $photos=@(Children $w | Where-Object {$_.Current.ClassName -like 'ChatLogContentPhoto*'} | Sort-Object {$_.Current.BoundingRectangle.Top})
   $scope='visible_room_photos'
   $failure=$null
   $images=@()
   try {
   for($index=$photos.Count-1;$index -ge 0 -and $images.Count -lt $request.limit;$index--){
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
   AssertRoomReady $w
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
   AssertRoomReady $w
   $button=SendButton $w
   AssertDraft $i ($prefix+$request.text)
   if($request.preview){@{ready=$true};break}
   $null=Room $request.room
   $script:dispatched=$true
   Invoke $button; Start-Sleep -Milliseconds 500
   if((Value (InputBox $w)).Current.Value){throw 'Mention send outcome uncertain; do not retry automatically'}
   @{sent=$true}
  }
  'read' {
   $w=Room $request.room
   $items=@(ChatItems $w)
   $messages=@(Messages $items)
   $unknown=@('member_count')
   if($messages | Where-Object {-not $_.sender}){$unknown+='sender'}
   if($messages | Where-Object {-not $_.date}){$unknown+='date'}
   # Only trust scroll state from the actual message list, never another panel.
   $item=$items | Select-Object -First 1
   $atEnd=$null
   while($item -and -not [System.Windows.Automation.Automation]::Compare($item,$w)){
    $scroll=$null
    if($item.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern,[ref]$scroll)){
     $atEnd=(-not $scroll.Current.VerticallyScrollable -or $scroll.Current.VerticalScrollPercent -ge 99.99)
     break
    }
    $item=[System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($item)
   }
   @{room_name=$w.Current.Name;hwnd=$w.Current.NativeWindowHandle;process_id=$request.process_id;member_count=$null;messages=$messages;dates=@();partial=$true;read_method='uia';unknown_fields=$unknown;at_end=$atEnd}
  }
  'copy' {
   $w=Room $request.room; Front $w
   $items=@(Children $w | Where-Object {$_.Current.ClassName -eq 'CommonChatItemDelegate'})
   if(-not $items.Count){@{raw_text=''};break}
   $message=$items | Where-Object {(VisibleCenter $w $_) -and $_.Current.Name -notmatch '^날짜 '} | Select-Object -Last 1
   if(-not $message){throw 'No visible message to copy'}
   $focused=$false
   if($message.Current.IsKeyboardFocusable){
    try {
     $message.SetFocus()
     $focused=[System.Windows.Automation.Automation]::Compare($message,[System.Windows.Automation.AutomationElement]::FocusedElement)
    } catch {$focused=$false}
   }
   if(-not $focused){Click $message}
   Add-Type -AssemblyName System.Windows.Forms
   Ctrl 65; Start-Sleep -Milliseconds 100
   $sequence=[KakaoInput]::GetClipboardSequenceNumber()
   Ctrl 67; Start-Sleep -Milliseconds 200
   $copied=[KakaoInput]::GetClipboardSequenceNumber()
   if($copied -eq $sequence -or [KakaoInput]::ClipboardOwnerPid() -ne $request.process_id){throw 'KakaoTalk did not copy a new transcript'}
   $text=[System.Windows.Forms.Clipboard]::GetText()
   if(-not $text){throw 'Message copy returned an empty clipboard'}
   if([KakaoInput]::GetClipboardSequenceNumber() -ne $copied){throw 'Clipboard changed during transcript copy'}
   @{raw_text=$text;before_copy=$sequence;after_copy=$copied}
  }
  'send' {
   $w=Room $request.room; $i=InputBox $w; $v=Value $i
   AssertRoomReady $w
   $before=@(Snapshot $w | Where-Object {$_.StartsWith($request.text+', ')}).Count
   if($v.Current.Value){throw 'Chat has an existing draft'}
   $v.SetValue($request.text)
   if($v.Current.Value -cne $request.text){throw 'Input verification failed'}
   if(Children $w | Where-Object {$_.Current.ClassName -like 'ClipboardImageWindow*'}){throw 'Image preview is open in target room'}
   AssertRoomReady $w
   $button=SendButton $w
   AssertDraft $i $request.text
   $null=Room $request.room
   $script:dispatched=$true
   Invoke $button; Start-Sleep -Milliseconds 500
   $fresh=Value (InputBox $w)
   if($fresh.Current.Value){throw 'Send outcome uncertain; do not retry automatically'}
   $after=@(Snapshot $w | Where-Object {$_.StartsWith($request.text+', ')}).Count
   if($after -le $before){
    $confirmed=WaitFor {if(@(Snapshot (Room $request.room) | Where-Object {$_.StartsWith($request.text+', ')}).Count -gt $before){$true}} 1800
    if(-not $confirmed){throw 'New message bubble not confirmed; do not retry automatically'}
   }
   @{sent=$true;confirmation='new_room_bubble'}
  }
  default {throw "Unknown action $($request.action)"}
 }
 @{success=$true;data=$result} | ConvertTo-Json -Depth 14 -Compress
} catch {
 $message=$_.Exception.Message
 $code=if($script:dispatched){'send_unconfirmed'}
       elseif($message -match 'multiple|ambiguous|found (?:[2-9]|[1-9][0-9]+)\b'){ 'room_ambiguous' }
       elseif($message -match 'existing draft'){ 'draft_exists' }
       elseif($message -match 'window changed|room changed'){ 'room_changed' }
       elseif($message -match 'found 0|room not found'){ 'room_not_found' }
       else{'target_not_verified'}
 @{success=$false;error=$message;before_send=(-not $script:dispatched);code=$code} | ConvertTo-Json -Compress
} finally {
 if($request.action -in 'copy','image','file','mention','download' -and $script:targetHwnd -and
     $previousWindow -ne [IntPtr]::Zero -and [KakaoInput]::InputTick() -eq $script:lastInput){
  $current=[KakaoInput]::GetForegroundWindow()
  if($current -eq [IntPtr]$script:roomHwnd -and $current -ne $previousWindow){
   [void][KakaoInput]::SetForegroundWindow($previousWindow)
  }
 }
}
}
while($null -ne ($line=[Console]::ReadLine())){
 try {
  $json=RunRequest (ConvertFrom-Json -InputObject $line)
  [Console]::WriteLine($json)
 } catch {
  [Console]::WriteLine((@{success=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress))
 }
}
