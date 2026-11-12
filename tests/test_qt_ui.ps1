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
           'Select-Object','Click','Key','Start-Sleep','PasteText','SendButton','AssertDraft','Invoke'
foreach($command in $clause.Item2.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true)){
 if($command.GetCommandName() -notin $allowed){throw "Unmocked command: $command"}
}
foreach($member in $clause.Item2.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true)){
 if($member.Extent.Text -ne '[regex]::Escape($request.mention)'){throw "Unmocked method: $member"}
}
$action = [scriptblock]::Create("switch ('mention') { 'mention' $body }")
function Room($name){if($name -cne 'fixture'){throw 'Wrong room'}; 'fixture'}
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
 if($command.GetCommandName() -notin 'Windows','Where-Object','Click','Start-Sleep'){throw "Unmocked command: $command"}
}
. ([scriptblock]::Create($openPhoto.Extent.Text))
$script:viewerOpen=$false
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
'2 isolated photo viewer scenarios passed'
