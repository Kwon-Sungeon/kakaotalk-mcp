$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName WindowsBase
function InitializeInput {
 if(('KakaoInput' -as [type]) -and ('KakaoUia' -as [type])){return}
 Add-Type -AssemblyName System.Drawing
 $refs=@(
  [System.Windows.Automation.AutomationElement].Assembly.Location,
  [System.Windows.Automation.ControlType].Assembly.Location,
  [System.Windows.Rect].Assembly.Location,
  'System.dll',
  'System.Core.dll',
  'System.Drawing.dll'
 )
 Add-Type -ReferencedAssemblies $refs -TypeDefinition @'
using System;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows;
using System.Windows.Automation;

public class KakaoInput {
 [StructLayout(LayoutKind.Sequential)] public struct Point {public int X; public int Y;}
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 [DllImport("user32.dll")] public static extern bool GetCursorPos(out Point p);
 public static Point Cursor(){Point p; if(!GetCursorPos(out p)) throw new InvalidOperationException("Cursor unavailable"); return p;}
 [StructLayout(LayoutKind.Sequential)] private struct LastInput { public uint Size; public uint Tick; }
 [DllImport("user32.dll")] private static extern bool GetLastInputInfo(ref LastInput info);
 public static uint InputTick(){var info=new LastInput{Size=8}; if(!GetLastInputInfo(ref info)) throw new InvalidOperationException("Input time unavailable"); return info.Tick;}
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
 [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h,out Rect rect);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out Rect rect);
 public static System.Drawing.Bitmap CaptureRendered(IntPtr hwnd,int logicalW,int logicalH,uint flags){
  int bufW=Math.Min(4096,Math.Max(logicalW*3,logicalW));
  int bufH=Math.Min(4096,Math.Max(logicalH*3,logicalH));
  var bmp=new System.Drawing.Bitmap(bufW,bufH,System.Drawing.Imaging.PixelFormat.Format32bppArgb);
  try {
   using(var g=System.Drawing.Graphics.FromImage(bmp)){
    g.Clear(System.Drawing.Color.FromArgb(255,254,1,253));
    IntPtr dc=g.GetHdc();
    try {if(!PrintWindow(hwnd,dc,flags)) throw new InvalidOperationException("Control capture failed");}
    finally {g.ReleaseHdc(dc);}
   }
   var rect=new System.Drawing.Rectangle(0,0,bufW,bufH);
   var data=bmp.LockBits(rect,System.Drawing.Imaging.ImageLockMode.ReadOnly,System.Drawing.Imaging.PixelFormat.Format32bppArgb);
   int maxX=-1,maxY=-1;
   try {
    int stride=Math.Abs(data.Stride);
    byte[] raw=new byte[stride*bufH];
    Marshal.Copy(data.Scan0,raw,0,raw.Length);
    for(int y=bufH-1;y>=0;y--){
     int row=y*stride;
     for(int x=bufW-1;x>=0;x--){
      int idx=row+x*4;
      if(!(raw[idx]==253 && raw[idx+1]==1 && raw[idx+2]==254)){
       if(y>maxY) maxY=y;
       if(x>maxX) maxX=x;
       break;
      }
     }
     if(maxY>=0 && maxX==bufW-1) break;
    }
    for(int y=0;y<=maxY && maxX<bufW-1;y++){
     int row=y*stride;
     for(int x=bufW-1;x>maxX;x--){
      int idx=row+x*4;
      if(!(raw[idx]==253 && raw[idx+1]==1 && raw[idx+2]==254)){maxX=x;break;}
     }
    }
   } finally {bmp.UnlockBits(data);}
   int cropW=Math.Max(1,Math.Min(bufW,(maxX>=0)?(maxX+1):logicalW));
   int cropH=Math.Max(1,Math.Min(bufH,(maxY>=0)?(maxY+1):logicalH));
   if(cropW==bufW && cropH==bufH) return bmp;
   var cropped=bmp.Clone(new System.Drawing.Rectangle(0,0,cropW,cropH),System.Drawing.Imaging.PixelFormat.Format32bppArgb);
   bmp.Dispose();
   return cropped;
  } catch {bmp.Dispose();throw;}
 }
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
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
  return PasteFile(room,edit,sequence,"");
 }
 public static bool PasteFile(IntPtr room,IntPtr edit,uint sequence,string hint){
  IntPtr result;
  bool emptyOrHint=FilenameEquals(edit,"") || (!string.IsNullOrEmpty(hint) && FilenameEquals(edit,hint));
  if(!(IsChild(room,edit) && emptyOrHint && GetClipboardSequenceNumber()==sequence)) return false;
  if(!string.IsNullOrEmpty(hint) && FilenameEquals(edit,hint)){
   SendMessageTimeoutW(edit,0xB1,IntPtr.Zero,new IntPtr(-1),2,500,out result);
  }
  return SendMessageTimeoutW(edit,0x302,IntPtr.Zero,IntPtr.Zero,2,1500,out result)!=IntPtr.Zero;
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
 [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h,ref Point p);
 [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(Point p);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,int x,int y,int d,UIntPtr e);
 public static void BinarizeDarkText(System.Drawing.Bitmap bmp){
  var rect=new System.Drawing.Rectangle(0,0,bmp.Width,bmp.Height);
  var data=bmp.LockBits(rect,System.Drawing.Imaging.ImageLockMode.ReadWrite,System.Drawing.Imaging.PixelFormat.Format32bppArgb);
  try {
   int bytes=Math.Abs(data.Stride)*bmp.Height;
   byte[] buf=new byte[bytes];
   Marshal.Copy(data.Scan0,buf,0,bytes);
   for(int i=0;i+3<bytes;i+=4){
    byte b=buf[i], g=buf[i+1], r=buf[i+2];
    byte v=(r<115 && g<115 && b<115) ? (byte)0 : (byte)255;
    buf[i]=v; buf[i+1]=v; buf[i+2]=v; buf[i+3]=255;
   }
   Marshal.Copy(buf,0,data.Scan0,bytes);
  } finally {
   bmp.UnlockBits(data);
  }
 }
}

public class KakaoUia {
 public sealed class BubbleParts {
  public string[] Bodies;
  public bool Photo;
  public bool File;
  public bool Video;
  public string FileName;
 }

 public sealed class SortedItem {
  public AutomationElement Element;
  public string Label;
  public double Top;
 }

 public sealed class RoomSummary {
  public string Title;
  public int Hwnd;
 }

 public sealed class ChatListEntry {
  public string Title;
  public string Name;
  public string RawLabel;
  public bool IsOffscreen;
 }

 private static readonly Regex SendButtonRegex = new Regex(
  @"^(전송|보내기|Send)(\([A-Za-z]\))?$", RegexOptions.Compiled | RegexOptions.IgnoreCase);
 private static readonly Regex ModalClassRegex = new Regex(
  @"(ClipboardImageWindow|File.*(Window|Dialog)|Confirm.*(Window|Dialog))", RegexOptions.Compiled | RegexOptions.IgnoreCase);
 private static readonly Regex ChatTitleRegex = new Regex(
  @"^(?:(?:팀채팅|그룹채팅|나와의 채팅), )?([^,]+)(?:, .*)?$", RegexOptions.Compiled);
 private static readonly Regex DrawerContainerRegex = new Regex(
  @"(대화상대|참여자|채팅방 서랍|서랍|Participant|Member|Drawer)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
 private static readonly Regex ParticipantSkipRegex = new Regex(
  @"^(?:대화상대 초대|초대|Invite|채팅방 나가기|나가기|Leave|더보기|More)$", RegexOptions.Compiled | RegexOptions.IgnoreCase);
 private static readonly Regex DrawerExactBtnRegex = new Regex(
  @"^(?:채팅방 서랍|서랍|대화상대|참여자|Chat Drawer|Drawer|Participants|Members)(?:\s*\d+)?$", RegexOptions.Compiled | RegexOptions.IgnoreCase);
 private static readonly Regex DrawerExcludeBtnRegex = new Regex(
  @"(초대|나가기|Invite|Leave|검색|Search|전송|보내기|Send|닫기|Close|파일|File|이모티콘|Emoticon)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
 private static readonly Regex MenuBtnRegex = new Regex(
  @"^(?:메뉴|Menu)$", RegexOptions.Compiled | RegexOptions.IgnoreCase);

 private static AutomationElementCollection FindAllRetry(
  AutomationElement root, TreeScope scope, Condition condition, CacheRequest cache) {
  for (int attempt = 0; attempt < 3; attempt++) {
   try {
    if (cache != null) {
     using (cache.Activate()) {
      return root.FindAll(scope, condition);
     }
    }
    return root.FindAll(scope, condition);
   } catch {
    if (attempt == 2) throw;
    Thread.Sleep(150);
   }
  }
  return null;
 }

 public static AutomationElement[] FindProcessWindows(int pid) {
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.ClassNameProperty);
  cache.Add(AutomationElement.NameProperty);
  cache.Add(AutomationElement.NativeWindowHandleProperty);
  var cond = new PropertyCondition(AutomationElement.ProcessIdProperty, pid);
  using (cache.Activate()) {
   var col = AutomationElement.RootElement.FindAll(TreeScope.Children, cond);
   var list = new AutomationElement[col.Count];
   for (int i = 0; i < col.Count; i++) list[i] = col[i];
   return list;
  }
 }

 public static AutomationElement FindMainWindow(int pid) {
  var wins = FindProcessWindows(pid);
  AutomationElement match = null;
  int count = 0;
  for (int i = 0; i < wins.Length; i++) {
   string cls = wins[i].Cached.ClassName ?? "";
   if (cls.StartsWith("MainWindow", StringComparison.OrdinalIgnoreCase)) {
    match = wins[i];
    count++;
   }
  }
  if (count != 1) throw new InvalidOperationException("KakaoTalk main window not found");
  return match;
 }

 public static AutomationElement[] FindChatWindows(int pid, string exactName) {
  var wins = FindProcessWindows(pid);
  var list = new List<AutomationElement>();
  for (int i = 0; i < wins.Length; i++) {
   string cls = wins[i].Cached.ClassName ?? "";
   if (cls.IndexOf("ChatWindow", StringComparison.OrdinalIgnoreCase) >= 0) {
    if (exactName == null || string.Equals(wins[i].Cached.Name, exactName, StringComparison.Ordinal)) {
     list.Add(wins[i]);
    }
   }
  }
  return list.ToArray();
 }

 public static RoomSummary[] GetRooms(int pid) {
  var wins = FindChatWindows(pid, null);
  var result = new RoomSummary[wins.Length];
  for (int i = 0; i < wins.Length; i++) {
   result[i] = new RoomSummary {
    Title = wins[i].Cached.Name ?? "",
    Hwnd = wins[i].Cached.NativeWindowHandle
   };
  }
  return result;
 }

 public static AutomationElement[] FindByClassPrefix(AutomationElement root, string prefix) {
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.ClassNameProperty);
  var col = FindAllRetry(root, TreeScope.Descendants, Condition.TrueCondition, cache);
  var list = new List<AutomationElement>();
  for (int i = 0; i < col.Count; i++) {
   var el = col[i];
   string cls = el.Cached.ClassName ?? "";
   if (cls.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) {
    list.Add(el);
   }
  }
  return list.ToArray();
 }

 public static AutomationElement[] FindSendButtons(AutomationElement container) {
  var cond = new AndCondition(
   new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button),
   new PropertyCondition(AutomationElement.IsEnabledProperty, true),
   new PropertyCondition(AutomationElement.IsOffscreenProperty, false));
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.NameProperty);
  var col = FindAllRetry(container, TreeScope.Descendants, cond, cache);
  var list = new List<AutomationElement>();
  for (int i = 0; i < col.Count; i++) {
   var el = col[i];
   string name = (el.Cached.Name ?? "").Replace("&", "");
   if (SendButtonRegex.IsMatch(name)) {
    list.Add(el);
   }
  }
  return list.ToArray();
 }

 public static bool HasRoomModal(AutomationElement room) {
  var cond = new PropertyCondition(AutomationElement.IsOffscreenProperty, false);
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.ClassNameProperty);
  var col = FindAllRetry(room, TreeScope.Descendants, cond, cache);
  for (int i = 0; i < col.Count; i++) {
   string cls = col[i].Cached.ClassName ?? "";
   if (ModalClassRegex.IsMatch(cls)) return true;
  }
  return false;
 }

 public static AutomationElement FindChatTab(AutomationElement main) {
  var cond = new OrCondition(
   new PropertyCondition(AutomationElement.NameProperty, "채팅"),
   new PropertyCondition(AutomationElement.NameProperty, "Chats"));
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.ClassNameProperty);
  var col = FindAllRetry(main, TreeScope.Descendants, cond, cache);
  for (int i = 0; i < col.Count; i++) {
   string cls = col[i].Cached.ClassName ?? "";
   if (cls.StartsWith("TabMenuButton", StringComparison.OrdinalIgnoreCase)) {
    return col[i];
   }
  }
  return null;
 }

 public static AutomationElement[] FindMatchingSearchItems(
  AutomationElement main, string roomName, string namePattern, bool selfOnly) {
  var cond = new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.ListItem);
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.NameProperty);
  var col = FindAllRetry(main, TreeScope.Descendants, cond, cache);
  var rx = new Regex(namePattern, RegexOptions.CultureInvariant);
  var list = new List<AutomationElement>();
  for (int i = 0; i < col.Count; i++) {
   string name = col[i].Cached.Name ?? "";
   if ((!selfOnly && string.Equals(name, roomName, StringComparison.Ordinal)) || rx.IsMatch(name)) {
    list.Add(col[i]);
   }
  }
  return list.ToArray();
 }

 public static AutomationElement[] FindChatItems(AutomationElement room) {
  var cond = new PropertyCondition(AutomationElement.ClassNameProperty, "CommonChatItemDelegate");
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.NameProperty);
  cache.Add(AutomationElement.BoundingRectangleProperty);
  var col = FindAllRetry(room, TreeScope.Descendants, cond, cache);
  var list = new AutomationElement[col.Count];
  for (int i = 0; i < col.Count; i++) list[i] = col[i];
  return list;
 }

 public static SortedItem[] SortChatItems(IEnumerable items) {
  var list = new List<SortedItem>();
  foreach (object obj in items) {
   var el = obj as AutomationElement;
   if (el == null) continue;
   string label;
   Rect rect;
   try {
    label = el.Cached.Name ?? "";
    rect = el.Cached.BoundingRectangle;
   } catch (InvalidOperationException) {
    label = el.Current.Name ?? "";
    rect = el.Current.BoundingRectangle;
   }
   list.Add(new SortedItem { Element = el, Label = label, Top = rect.Top });
  }
  list.Sort((a, b) => a.Top.CompareTo(b.Top));
  return list.ToArray();
 }

 public static BubbleParts GetBubbleParts(AutomationElement item) {
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.ClassNameProperty);
  cache.Add(AutomationElement.NameProperty);
  var col = FindAllRetry(item, TreeScope.Descendants, Condition.TrueCondition, cache);
  var bodies = new List<string>();
  bool photo = false, file = false, video = false;
  string fileName = null;
  for (int i = 0; i < col.Count; i++) {
   var child = col[i];
   string cls = child.Cached.ClassName ?? "";
   if (cls.StartsWith("TalkObjectText", StringComparison.OrdinalIgnoreCase)) {
    object pat;
    if (child.TryGetCurrentPattern(TextPattern.Pattern, out pat)) {
     bodies.Add(((TextPattern)pat).DocumentRange.GetText(-1));
    }
   } else if (cls.StartsWith("ChatLogContentPhoto", StringComparison.OrdinalIgnoreCase)) {
    photo = true;
   } else if (cls.StartsWith("ChatLogContentFile", StringComparison.OrdinalIgnoreCase)) {
    file = true;
   } else if (cls.IndexOf("ChatLogContentVideo", StringComparison.OrdinalIgnoreCase) >= 0) {
    video = true;
   } else if (fileName == null && cls.StartsWith("EmojiText", StringComparison.OrdinalIgnoreCase)) {
    fileName = child.Cached.Name;
   }
  }
  return new BubbleParts {
   Bodies = bodies.ToArray(),
   Photo = photo,
   File = file,
   Video = video,
   FileName = fileName
  };
 }

 public static ChatListEntry[] FindChatListEntries(AutomationElement main) {
  var cond = new OrCondition(
   new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.ListItem),
   new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.DataItem));
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.NameProperty);
  cache.Add(AutomationElement.ClassNameProperty);
  cache.Add(AutomationElement.IsOffscreenProperty);
  var col = FindAllRetry(main, TreeScope.Descendants, cond, cache);
  var entries = new List<ChatListEntry>();
  for (int i = 0; i < col.Count; i++) {
   var el = col[i];
   string cls = el.Cached.ClassName ?? "";
   if (cls.StartsWith("TextField", StringComparison.OrdinalIgnoreCase) ||
       cls.StartsWith("TabMenuButton", StringComparison.OrdinalIgnoreCase)) continue;
   string raw = el.Cached.Name ?? "";
   if (string.IsNullOrWhiteSpace(raw)) continue;
   var m = ChatTitleRegex.Match(raw);
   string title = m.Success ? m.Groups[1].Value : raw;
   entries.Add(new ChatListEntry {
    Title = title,
    Name = title,
    RawLabel = raw,
    IsOffscreen = el.Cached.IsOffscreen
   });
  }
  return entries.ToArray();
 }

 public static string[] FindParticipantNames(AutomationElement container, bool requireDrawerContainer) {
  var itemCond = new OrCondition(
   new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.ListItem),
   new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.DataItem));
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.NameProperty);
  cache.Add(AutomationElement.ClassNameProperty);

  var targets = new List<AutomationElement>();
  if (requireDrawerContainer) {
   var allCache = new CacheRequest();
   allCache.TreeScope = TreeScope.Element;
   allCache.Add(AutomationElement.NameProperty);
   allCache.Add(AutomationElement.ClassNameProperty);
   allCache.Add(AutomationElement.ControlTypeProperty);
   allCache.Add(AutomationElement.IsOffscreenProperty);
   var all = FindAllRetry(container, TreeScope.Descendants, Condition.TrueCondition, allCache);
   for (int i = 0; i < all.Count; i++) {
    var el = all[i];
    if (el.Cached.IsOffscreen || el.Cached.ControlType == ControlType.Button) continue;
    string cls = el.Cached.ClassName ?? "";
    string nm = el.Cached.Name ?? "";
    if (string.Equals(cls, "CommonChatItemDelegate", StringComparison.Ordinal)) continue;
    if (DrawerContainerRegex.IsMatch(cls) || DrawerContainerRegex.IsMatch(nm)) {
     targets.Add(el);
    }
   }
  } else {
   targets.Add(container);
  }

  var seen = new HashSet<string>(StringComparer.Ordinal);
  var results = new List<string>();
  for (int t = 0; t < targets.Count; t++) {
   var col = FindAllRetry(targets[t], TreeScope.Descendants, itemCond, cache);
   for (int i = 0; i < col.Count; i++) {
    var el = col[i];
    string cls = el.Cached.ClassName ?? "";
    if (string.Equals(cls, "CommonChatItemDelegate", StringComparison.Ordinal) ||
        cls.StartsWith("TabMenuButton", StringComparison.OrdinalIgnoreCase) ||
        cls.StartsWith("TextField", StringComparison.OrdinalIgnoreCase)) continue;
    string name = (el.Cached.Name ?? "").Trim();
    if (name.Length == 0 || ParticipantSkipRegex.IsMatch(name)) continue;
    if (seen.Add(name)) results.Add(name);
   }
  }
  return results.ToArray();
 }

 public static AutomationElement FindDrawerButton(AutomationElement room) {
  var cond = new AndCondition(
   new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button),
   new PropertyCondition(AutomationElement.IsEnabledProperty, true),
   new PropertyCondition(AutomationElement.IsOffscreenProperty, false));
  var cache = new CacheRequest();
  cache.TreeScope = TreeScope.Element;
  cache.Add(AutomationElement.NameProperty);
  var col = FindAllRetry(room, TreeScope.Descendants, cond, cache);
  AutomationElement partialMatch = null;
  AutomationElement menuMatch = null;
  for (int i = 0; i < col.Count; i++) {
   string name = (col[i].Cached.Name ?? "").Replace("&", "").Trim();
   if (name.Length == 0) continue;
   if (DrawerExactBtnRegex.IsMatch(name)) return col[i];
   if (partialMatch == null && DrawerContainerRegex.IsMatch(name) && !DrawerExcludeBtnRegex.IsMatch(name)) {
    partialMatch = col[i];
   } else if (menuMatch == null && MenuBtnRegex.IsMatch(name)) {
    menuMatch = col[i];
   }
  }
  return partialMatch ?? menuMatch;
 }

 public static Hashtable InspectTree(AutomationElement root, int maxDepth, int maxNodes) {
  int visited = 0;
  var sb = new StringBuilder();
  var node = WalkNode(TreeWalker.ControlViewWalker, root, 0, maxDepth, maxNodes, ref visited, sb, false);
  var res = new Hashtable();
  res["node"] = node;
  res["tree_text"] = sb.ToString();
  res["truncated"] = visited >= maxNodes;
  return res;
 }

 private static Hashtable WalkNode(
  TreeWalker walker, AutomationElement el, int depth, int maxDepth, int maxNodes,
  ref int visited, StringBuilder sb, bool insideChatBubble) {
  visited++;
  AutomationElement.AutomationElementInformation cur;
  try {
   cur = el.Current;
  } catch {
   return null;
  }
  string cls = cur.ClassName ?? "";
  string ctype = cur.ControlType != null ? cur.ControlType.ProgrammaticName : "";
  string aid = cur.AutomationId ?? "";
  bool isBubble = insideChatBubble ||
   string.Equals(cls, "CommonChatItemDelegate", StringComparison.Ordinal) ||
   cls.StartsWith("TalkObjectText", StringComparison.OrdinalIgnoreCase);
  string name = isBubble ? "[redacted]" : (cur.Name ?? "");

  sb.Append(' ', depth * 2).Append(ctype).Append(" class=").Append(cls);
  if (aid.Length > 0) sb.Append(" id=").Append(aid);
  if (name.Length > 0) sb.Append(" name=").Append(name.Length > 80 ? name.Substring(0, 80) + "..." : name);
  sb.AppendLine();

  var children = new List<Hashtable>();
  if (depth < maxDepth && visited < maxNodes && !isBubble) {
   AutomationElement child = null;
   try { child = walker.GetFirstChild(el); } catch {}
   while (child != null && visited < maxNodes) {
    var walked = WalkNode(walker, child, depth + 1, maxDepth, maxNodes, ref visited, sb, isBubble);
    if (walked != null) children.Add(walked);
    try { child = walker.GetNextSibling(child); } catch { child = null; }
   }
  }
  var dict = new Hashtable();
  dict["class_name"] = cls;
  dict["control_type"] = ctype;
  dict["automation_id"] = aid;
  dict["name"] = name;
  dict["is_enabled"] = cur.IsEnabled;
  dict["is_offscreen"] = cur.IsOffscreen;
  dict["children"] = children.ToArray();
  return dict;
 }
}
'@
 [void][KakaoInput]::SetProcessDPIAware()
}
function InitializeUia { InitializeInput }
function InitializeForms {
 if($script:formsLoaded){return}
 Add-Type -AssemblyName System.Windows.Forms
 $script:formsLoaded=$true
}
function InitializeOcr {
 if($script:ocrInitialized){return}
 Add-Type -AssemblyName System.Drawing
 Add-Type -AssemblyName System.Runtime.WindowsRuntime
 $null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
 $null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
 $null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
 $null=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
 $null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
 $null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
 $script:ocrAsTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
 } | Select-Object -First 1
 $script:ocrEngine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ko-KR'))
 $script:ocrInitialized=$true
}
function AwaitOcr($op,[Type]$type){
 $task=$script:ocrAsTask.MakeGenericMethod($type).Invoke($null,@($op))
 $task.GetAwaiter().GetResult()
}
function Children($element) {
 for($attempt=0;$attempt -lt 3;$attempt++){
  try {return @($element.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition))}
  catch {if($attempt -eq 2){throw};Start-Sleep -Milliseconds 150}
 }
}
function Windows {
 if('KakaoUia' -as [type]){return [KakaoUia]::FindProcessWindows([int]$request.process_id)}
 $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,[int]$request.process_id)
 @([System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$condition))
}
function Main {
 if('KakaoUia' -as [type]){return [KakaoUia]::FindMainWindow([int]$request.process_id)}
 $w = @(Windows | Where-Object {$_.Current.ClassName -like 'MainWindow*'})
 if($w.Count -ne 1){throw 'KakaoTalk main window not found'}
 $w[0]
}
function Room($name) {
 $w = if('KakaoUia' -as [type]){@([KakaoUia]::FindChatWindows([int]$request.process_id,$name))}else{@(Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow' -and $_.Current.Name -ceq $name})}
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
 InitializeForms
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
 $dlgCond=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,'#32770')
 $dialog=$viewer.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$dlgCond)
 if(-not $dialog){
  $saveBtn=$viewer.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'저장 Ctrl+S'))
  Invoke $saveBtn
  Start-Sleep -Milliseconds 500
  $dialog=$viewer.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$dlgCond)
 }
 if(-not $dialog){throw 'Photo save dialog not found'}
 try {
  $field=$dialog.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.AndCondition]::new(
   [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty,'1001'),
   [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit)
  ))
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
  $okBtn=$dialog.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.AndCondition]::new(
   [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty,'1'),
   [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)
  ))
  Invoke $okBtn
  $deadline=[DateTime]::UtcNow.AddSeconds(5)
  while(-not (Test-Path -LiteralPath $path) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100}
  if(-not (Test-Path -LiteralPath $path)){throw 'Photo save did not create the requested file'}
  return $path
 } catch {
  $cancel=$dialog.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.AndCondition]::new(
   [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty,'2'),
   [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)
  ))
  if($cancel){Invoke $cancel}
  throw
 }
}
function InputBox($w){
 $v=if('KakaoUia' -as [type]){@([KakaoUia]::FindByClassPrefix($w,'TextObjectTextArea'))}else{@(Children $w | Where-Object {$_.Current.ClassName -like 'TextObjectTextArea*'})}
 if($v.Count -ne 1){throw 'Unique chat input not found'}
 $v[0]
}
function SendButton($container) {
 $buttons=if('KakaoUia' -as [type]){@([KakaoUia]::FindSendButtons($container))}else{@(Children $container | Where-Object {
  $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
  $_.Current.Name.Replace('&','') -match '^(전송|보내기|Send)(\([A-Za-z]\))?$' -and $_.Current.IsEnabled -and -not $_.Current.IsOffscreen
 })}
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
  InitializeForms
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
   InitializeForms
   $paths=[System.Windows.Forms.Clipboard]::GetFileDropList()
   $hint=if($request.literal_hint){[string]$request.literal_hint}else{''}
   $emptyOrHint=[KakaoInput]::FilenameEquals($edit,'') -or ($hint -and [KakaoInput]::FilenameEquals($edit,$hint))
   if($paths.Count -ne 1 -or $paths[0] -cne $request.path -or
      -not $emptyOrHint -or
      [KakaoInput]::GetClipboardSequenceNumber() -ne $request.sequence){throw 'Legacy draft or file clipboard changed'}
   $null=FileRoom
   if(-not [KakaoInput]::IsChild([IntPtr]$request.hwnd,$edit)){throw 'Legacy input changed'}
   $script:dispatched=$true
   if(-not [KakaoInput]::PasteFile([IntPtr]$request.hwnd,$edit,$request.sequence,$hint)){throw 'Could not submit file paste'}
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
 $script:dispatched=$true
 if(-not (TryInvoke $ok[0])){
  if($ok[0].Current.ClassName -eq 'Button' -and $ok[0].Current.NativeWindowHandle){
   Front $picker
   if(-not [KakaoInput]::PostMessageW([IntPtr]$ok[0].Current.NativeWindowHandle,0xF5,[IntPtr]::Zero,[IntPtr]::Zero)){throw 'Could not submit selected file'}
  }else{Invoke $ok[0]}
 }
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
function FindLegacyPopupSendCenter($lines, [int]$clientHeight) {
 $sendBtnRx='^(?:\d+\s*(?:개|장|건)?\s*)?(?:전송|보내기|Send)(?:\s*\(\d+\))?$'
 $hits=@()
 foreach($ln in $lines){
  if($ln.cy -lt ($clientHeight * 0.48)){continue}
  $lt=([string]$ln.text).Trim()
  if($lt -match $sendBtnRx){
   $hits+=@{cx=[int]$ln.cx;cy=[int]$ln.cy}
   continue
  }
  foreach($wd in @($ln.words)){
   $wt=([string]$wd.text).Trim()
   if($wt -match '^(?:전송|보내기|Send)$'){
    $hits+=@{cx=[int][Math]::Round($wd.x+$wd.w/2.0);cy=[int][Math]::Round($wd.y+$wd.h/2.0)}
   }
  }
 }
 if($hits.Count -eq 1){return $hits[0]}
 return $null
}
function ClickLegacyPopupPoint([IntPtr]$phwnd, $preview, [int]$cx, [int]$cy) {
 $pt=[KakaoInput+Point]::new()
 $pt.X=$cx; $pt.Y=$cy
 if(-not [KakaoInput]::ClientToScreen($phwnd,[ref]$pt)){return $false}
 Front $preview
 $hitHwnd=[KakaoInput]::WindowFromPoint($pt)
 if([KakaoInput]::GetForegroundWindow() -ne $phwnd -or ($hitHwnd -ne $phwnd -and -not [KakaoInput]::IsChild($phwnd,$hitHwnd))){return $false}
 $prev=[KakaoInput]::Cursor()
 try {
  if(-not [KakaoInput]::SetCursorPos($pt.X,$pt.Y)){return $false}
  Start-Sleep -Milliseconds 25
  if(-not [KakaoInput]::CursorAt($pt.X,$pt.Y)){return $false}
  $script:dispatched=$true
  [KakaoInput]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
  [KakaoInput]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
  $script:lastInput=[KakaoInput]::InputTick()
  return $true
 } finally {
  if([KakaoInput]::CursorAt($pt.X,$pt.Y)){[void][KakaoInput]::SetCursorPos($prev.X,$prev.Y)}
 }
}
function ConfirmLegacyAttachmentPopup($preview, [string]$kind, [string]$expectedFileName='') {
 $null=FileRoom
 $phwnd=[IntPtr]$preview.Current.NativeWindowHandle
 if($phwnd -eq [IntPtr]::Zero -or $preview.Current.ClassName -ne 'EVA_Window_Dblclk'){return $false}
 $cr=[KakaoInput+Rect]::new()
 if(-not [KakaoInput]::GetClientRect($phwnd,[ref]$cr) -or $cr.Bottom -lt 40){return $false}
 $lines=@(CaptureControlLines $phwnd $phwnd $request.process_id 1)
 $joined=($lines | ForEach-Object {[string]$_.text}) -join "`n"
 $compact=($joined -replace '[\s_\-]+','').ToLowerInvariant()
 if($kind -eq 'file'){
  $normName=($expectedFileName -replace '[\s_\-]+','').ToLowerInvariant()
  if($joined -notmatch '(파일\s*전송|파일\s*보내기|Send\s*file|File\s*transfer)' -or $joined -match '(?:외\s*\d+\s*개|\b[2-9]\s*개\s*전송|\b\d{2,}\s*개\s*전송)' -or -not $normName -or -not $compact.Contains($normName)){
   return $false
  }
 } else {
  if($joined -match '(나가|삭제|차단|신고|Leave|Delete|Block|Report)'){return $false}
  if($joined -notmatch '(사진|이미지|클립보드|전송|보내기|Photo|Image|Clipboard|Send)'){
   return $false
  }
 }
 $null=FileRoom
 $cur=OwnedPopup
 if(-not $cur -or [IntPtr]$cur.Current.NativeWindowHandle -ne $phwnd){throw 'Attachment preview changed'}
 $targetHit=FindLegacyPopupSendCenter $lines $cr.Bottom
 if(-not $targetHit){
  $hcLines=@(CaptureControlLines $phwnd $phwnd $request.process_id 2 $true)
  $targetHit=FindLegacyPopupSendCenter $hcLines $cr.Bottom
 }
 if($targetHit){
  if(ClickLegacyPopupPoint $phwnd $preview $targetHit.cx $targetHit.cy){
   if(WaitFor {if(-not (OwnedPopup) -and (FileRoom).Current.IsEnabled){$true}} 2200){return $true}
  }
 }
 $cur=OwnedPopup
 if($cur -and [IntPtr]$cur.Current.NativeWindowHandle -eq $phwnd){
  Front $preview
  $script:dispatched=$true
  Key 13
  if(WaitFor {if(-not (OwnedPopup) -and (FileRoom).Current.IsEnabled){$true}} 1200){return $true}
 }
 return (-not (OwnedPopup) -and (FileRoom).Current.IsEnabled)
}
function SendFile {
 $w=FileRoom
 $name=[IO.Path]::GetFileName($request.path)
 $existingPreview=FilePreview
 if($existingPreview){
  if(-not $request.qt -and $existingPreview.Current.ClassName -eq 'EVA_Window_Dblclk' -and (ConfirmLegacyAttachmentPopup $existingPreview 'file' $name)){
   return @{sent=$true;confirmation='verified_file_preview_closed'}
  }
  throw 'Close the existing file preview before attaching'
 }
 $before=0
 $bubbles={
  $w=FileRoom
  if('KakaoUia' -as [type]){
   $nameCond=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,$name)
   @([KakaoUia]::FindByClassPrefix($w,'ChatLogContentFile') | Where-Object {$null -ne $_.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$nameCond)}).Count
  } else {
   @(Children $w | Where-Object {$_.Current.ClassName -like 'ChatLogContentFile*'} | Where-Object {
     @(Children $_ | Where-Object {$_.Current.Name -ceq $name}).Count -gt 0
   }).Count
  }
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
   }elseif(-not $request.qt -and $preview.Current.ClassName -eq 'EVA_Window_Dblclk'){
    if(ConfirmLegacyAttachmentPopup $preview 'file' $name){
     return @{sent=$true;confirmation='verified_file_preview_closed'}
    }
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
 $hasModal=if('KakaoUia' -as [type]){[KakaoUia]::HasRoomModal($w)}else{[bool](Children $w | Where-Object {-not $_.Current.IsOffscreen -and $_.Current.ClassName -match '(ClipboardImageWindow|File.*(Window|Dialog)|Confirm.*(Window|Dialog))'} | Select-Object -First 1)}
 if($hasModal){throw 'Room has an open preview or confirmation dialog'}
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
 $mainHwnd=[IntPtr]$request.hwnd
 $listHwnd=[IntPtr]$request.list_hwnd
 $editHwnd=[IntPtr]$request.edit_hwnd
 $main=[System.Windows.Automation.AutomationElement]::FromHandle($mainHwnd)
 $list=[System.Windows.Automation.AutomationElement]::FromHandle($listHwnd)
 if($main.Current.ProcessId -ne $request.process_id -or $list.Current.ProcessId -ne $request.process_id -or
    -not [KakaoInput]::IsChild($mainHwnd,$listHwnd)){throw 'Search list owner changed'}
 $anyCond=[System.Windows.Automation.OrCondition]::new(
  [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::ListItem),
  [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::DataItem)
 )
 $item=$null
 if($null -ne $list.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$anyCond)){
  $item=WaitFor {ExactItem $list $request.room} 1500
  if(-not $item){throw 'Cannot verify exact room in legacy search results; open the room manually'}
  if(-not [KakaoInput]::FilenameEquals($editHwnd,$request.room)){throw 'Search text changed'}
  $current=ExactItem $list $request.room
  if(-not $current -or -not [System.Windows.Automation.Automation]::Compare($item,$current)){throw 'Room search results changed'}
  $pattern=$null
  if($current.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)){$pattern.Invoke()}
  else{Front $main; Click $current $true}
  return @{opened=$true;method='uia'}
 }
 $match=$null
 $deadline=[DateTime]::UtcNow.AddMilliseconds(1600)
 $nonEmptyPolls=0
 while($true){
  if(-not [KakaoInput]::FilenameEquals($editHwnd,$request.room)){throw 'Search text changed'}
  $lines=@(CaptureControlLines $mainHwnd $listHwnd $request.process_id)
  $res=FindOcrRoomMatch $lines $request.room
  if($res.ambiguous){throw 'Ambiguous exact room in legacy search results; open the room manually'}
  if($res.match){$match=$res.match;break}
  if($lines.Count -gt 0){$nonEmptyPolls++}
  if([DateTime]::UtcNow -ge $deadline -or $nonEmptyPolls -ge 3){break}
  Start-Sleep -Milliseconds 120
 }
 if(-not $match){
  $preview=(@($lines | Select-Object -First 6 | ForEach-Object { "$($_.x):$($_.text)" }) -join ' | ')
  throw "Cannot verify exact room in legacy search results (ocr=$($lines.Count): $preview); open the room manually"
 }
 if(-not [KakaoInput]::FilenameEquals($editHwnd,$request.room)){throw 'Search text changed'}
 Front $main
 $pt=[KakaoInput+Point]::new()
 $pt.X=[int]$match.cx; $pt.Y=[int]$match.cy
 if(-not [KakaoInput]::ClientToScreen($listHwnd,[ref]$pt)){throw 'Cannot map search item coordinates'}
 $hit=[KakaoInput]::WindowFromPoint($pt)
 if($hit -ne $listHwnd -and $hit -ne $mainHwnd -and -not [KakaoInput]::IsChild($mainHwnd,$hit)){throw 'Search result is covered'}
 $previous=[KakaoInput]::Cursor()
 try {
  if(-not [KakaoInput]::SetCursorPos($pt.X,$pt.Y)){throw 'Could not position cursor'}
  Start-Sleep -Milliseconds 30
  for($n=0;$n -lt 2;$n++){
   if(-not [KakaoInput]::CursorAt($pt.X,$pt.Y)){throw 'Pointer moved before click'}
   [KakaoInput]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
   [KakaoInput]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
   $script:lastInput=[KakaoInput]::InputTick()
   if($n -eq 0){Start-Sleep -Milliseconds 40}
  }
 } finally {
  if([KakaoInput]::CursorAt($pt.X,$pt.Y)){[void][KakaoInput]::SetCursorPos($previous.X,$previous.Y)}
 }
 Start-Sleep -Milliseconds 150
 @{opened=$true;method='win32_ocr'}
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
 $tab=if('KakaoUia' -as [type]){[KakaoUia]::FindChatTab($w)}else{Children $w | Where-Object {$_.Current.Name -in '채팅','Chats' -and $_.Current.ClassName -like 'TabMenuButton*'} | Select-Object -First 1}
 if(-not $tab){return}
 $sel=$null
 if($tab.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$sel)){
  if($sel.Current.IsSelected){return}
 }
 $tog=$null
 if($tab.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$tog)){
  if($tog.Current.ToggleState -eq [System.Windows.Automation.ToggleState]::On){return}
 }
 Invoke $tab
 Start-Sleep -Milliseconds 200
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
 if('KakaoUia' -as [type]){return [KakaoUia]::FindChatItems($w)}
 $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ClassNameProperty,'CommonChatItemDelegate')
 $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)
}
function BubbleParts($element) {
 $aeType='System.Windows.Automation.AutomationElement' -as [type]
 if(('KakaoUia' -as [type]) -and $null -ne $aeType -and ($element -is $aeType)){
  return [KakaoUia]::GetBubbleParts($element)
 }
 $parts=@(Children $element)
 $bodyEls=@($parts | Where-Object {$_.Current.ClassName -like 'TalkObjectText*'})
 $bodies=@(if($bodyEls.Count){$bodyEls | ForEach-Object {$_.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText(-1)}})
 $photo=[bool]($parts | Where-Object {$_.Current.ClassName -like 'ChatLogContentPhoto*'})
 $file=[bool]($parts | Where-Object {$_.Current.ClassName -like 'ChatLogContentFile*'})
 $video=[bool]($parts | Where-Object {$_.Current.ClassName -match 'ChatLogContentVideo'})
 $filename=$parts | Where-Object {$_.Current.ClassName -like 'EmojiText*'} | Select-Object -First 1
 $fileNameText=if($filename){$filename.Current.Name}else{$null}
 @{Bodies=$bodies;Photo=$photo;File=$file;Video=$video;FileName=$fileNameText}
}
function Messages($items) {
 $messages=@(); $date=$null
 $aeType='System.Windows.Automation.AutomationElement' -as [type]
 $sorted=if(('KakaoUia' -as [type]) -and $items.Count -and $null -ne $aeType -and ($items[0] -is $aeType)){
  [KakaoUia]::SortChatItems($items)
 } else {
  $items | Sort-Object {$_.Current.BoundingRectangle.Top} | ForEach-Object {@{Element=$_;Label=$_.Current.Name}}
 }
 foreach($entry in $sorted) {
  $sender=$null
  $label=$entry.Label
  if($label -match '^날짜 (.+)$' -or $label -match '^(?:Date |날짜 )?((?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})$'){$date=$Matches[1];continue}
  if($label -notmatch ', ((?:오전|오후) \d{1,2}:\d{2}|\d{1,2}:\d{2}\s*[AaPp][Mm]|\d{1,2}:\d{2})$'){continue}
  $time=$Matches[1]; $text=$label.Substring(0,$label.Length-$time.Length-2)
  $bp=BubbleParts $entry.Element
  $bodies=@($bp.Bodies)
  if($bodies.Count){$text=$bodies -join "`n"}
  if($bodies.Count -and $label.EndsWith(', '+$text+', '+$time,[StringComparison]::Ordinal)){
   $sender=$label.Substring(0,$label.Length-$text.Length-$time.Length-4)
  }
  $photo=[bool]$bp.Photo
  $file=[bool]$bp.File
  $video=[bool]$bp.Video
  if($file){if($bp.FileName){$text='파일: '+$bp.FileName}else{$text='파일'}}
  if($photo){$text='사진'}
  if($video){$text='동영상'}
  $urls=@([regex]::Matches($text,'https?://[^\s<>"'')\]]+') | ForEach-Object {$_.Value})
  $outgoing=$null
  if($bodies.Count -and $label -ceq ($text+', '+$time)){$outgoing=$true}
  elseif($bodies.Count -and $sender){$outgoing=$false}
  $messages+=@{outgoing=$outgoing;sender=$sender;time=$time;text=$text;date=$date;is_photo=$photo;is_file=$file;is_video=$video;urls=$urls}
 }
 $messages
}
function MessageListScroll($w, $items) {
 $item=$items | Select-Object -First 1
 if(-not $item){return $null}
 $walker=[System.Windows.Automation.TreeWalker]::RawViewWalker
 while($item -and -not [System.Windows.Automation.Automation]::Compare($item,$w)){
  $scroll=$null
  if($item.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern,[ref]$scroll)){
   return $scroll
  }
  $item=$walker.GetParent($item)
 }
 return $null
}
function MessageListAtEnd($w, $items) {
 $scroll=MessageListScroll $w $items
 if(-not $scroll){return $null}
 return (-not $scroll.Current.VerticallyScrollable -or $scroll.Current.VerticalScrollPercent -ge 99.9)
}
function MessageKey($m) {
 "$($m.outgoing)|$($m.sender)|$($m.time)|$($m.text)|$([bool]$m.is_photo)|$([bool]$m.is_file)|$([bool]$m.is_video)"
}
function MergeScrollPages($pages) {
 $merged=@()
 foreach($page in $pages){
  $items=@($page)
  if(-not $items.Count){continue}
  if(-not $merged.Count){$merged=$items;continue}
  $maxOverlap=[Math]::Min($merged.Count,$items.Count)
  $overlap=0
  for($k=$maxOverlap;$k -ge 1;$k--){
   $allMatch=$true
   for($j=0;$j -lt $k;$j++){
    if((MessageKey $merged[$merged.Count-$k+$j]) -cne (MessageKey $items[$j])){
     $allMatch=$false;break
    }
   }
   if($allMatch){$overlap=$k;break}
  }
  for($j=0;$j -lt $overlap;$j++){
   if(-not $items[$j].date -and $merged[$merged.Count-$overlap+$j].date){
    $items[$j].date=$merged[$merged.Count-$overlap+$j].date
   }
  }
  if($overlap -lt $merged.Count){
   $merged=@($merged[0..($merged.Count-$overlap-1)]) + $items
  } else {
   $merged=$items
  }
 }
 $lastDate=$null
 foreach($m in $merged){
  if($m.date){$lastDate=$m.date}
  elseif($lastDate){$m.date=$lastDate}
 }
 $merged
}
function FindNewOutgoingBubble($before, $after, $expectedText) {
 $beforeArr=@($before)
 $afterArr=@($after)
 if(-not $afterArr.Count){return $null}
 $added=$null
 if($beforeArr.Count -eq 0){
  if($afterArr.Count -eq 1){$added=@($afterArr[0])}
 } else {
  $prefixMatches=($afterArr.Count -gt $beforeArr.Count)
  if($prefixMatches){
   for($k=0;$k -lt $beforeArr.Count;$k++){
    if((MessageKey $beforeArr[$k]) -cne (MessageKey $afterArr[$k])){$prefixMatches=$false;break}
   }
  }
  if($prefixMatches){
   $added=@($afterArr[$beforeArr.Count..($afterArr.Count-1)])
  } elseif($beforeArr.Count -ge 2) {
   $anchorKey=MessageKey $beforeArr[$beforeArr.Count-1]
   $anchorCount=0
   foreach($m in $beforeArr){if((MessageKey $m) -ceq $anchorKey){$anchorCount++}}
   if($anchorCount -eq 1){
    $idx=-1
    for($k=0;$k -lt $afterArr.Count;$k++){
     if((MessageKey $afterArr[$k]) -ceq $anchorKey){$idx=$k;break}
    }
    $overlap=$idx+1
    if($idx -ge 1 -and $overlap -le $beforeArr.Count -and $overlap -lt $afterArr.Count){
     $tailMatch=$true
     for($j=0;$j -lt $overlap;$j++){
      if((MessageKey $beforeArr[$beforeArr.Count-$overlap+$j]) -cne (MessageKey $afterArr[$j])){$tailMatch=$false;break}
     }
     if($tailMatch){$added=@($afterArr[$overlap..($afterArr.Count-1)])}
    }
   }
  }
 }
 if($added -and $added.Count -eq 1){
  $b=$added[0]
  if($b.outgoing -ne $false -and -not $b.is_photo -and -not $b.is_file -and $b.text -ceq $expectedText){
   return $b
  }
 }
 return $null
}
# Windows OCR pipeline adapted from Contentrium's Kakao-Bridge (MIT).
function RecognizeBitmapLines($bitmap) {
 InitializeOcr
 if(-not $script:ocrEngine){throw 'Windows Korean OCR is unavailable; use complete mode'}
 $stream=$null; $output=$null; $software=$null
 try {
  $stream=[Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
  $output=[IO.WindowsRuntimeStreamExtensions]::AsStreamForWrite($stream)
  $bitmap.Save($output,[Drawing.Imaging.ImageFormat]::Png); $output.Flush(); $stream.Seek(0)
  $decoder=AwaitOcr ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $software=AwaitOcr ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $ocr=AwaitOcr ($script:ocrEngine.RecognizeAsync($software)) ([Windows.Media.Ocr.OcrResult])
  foreach($ln in $ocr.Lines){
   $words=@()
   $minX=[double]::MaxValue; $minY=[double]::MaxValue; $maxX=0.0; $maxY=0.0
   foreach($w in $ln.Words){
    $r=$w.BoundingRect
    $wx=[double]$r.X; $wy=[double]$r.Y; $ww=[double]$r.Width; $wh=[double]$r.Height
    if($wx -lt $minX){$minX=$wx}; if($wy -lt $minY){$minY=$wy}
    if(($wx+$ww) -gt $maxX){$maxX=$wx+$ww}; if(($wy+$wh) -gt $maxY){$maxY=$wy+$wh}
    $words+=@{text=$w.Text;x=$wx;y=$wy;w=$ww;h=$wh}
   }
   if($words.Count){
    @{
     text=$ln.Text
     x=[int][Math]::Round($minX); y=[int][Math]::Round($minY)
     w=[int][Math]::Round($maxX-$minX); h=[int][Math]::Round($maxY-$minY)
     cx=[int][Math]::Round(($minX+$maxX)/2.0); cy=[int][Math]::Round(($minY+$maxY)/2.0)
     words=$words
    }
   }
  }
 } finally {
  if($software){$software.Dispose()}
  if($output){$output.Dispose()}
  if($stream){$stream.Dispose()}
 }
}
function RecognizeBitmap($bitmap) {
 @(RecognizeBitmapLines $bitmap) | ForEach-Object {$_.text}
}
function CaptureControlLines([IntPtr]$ownerHwnd, [IntPtr]$ctrlHwnd, [int]$processId, [int]$scale=1, [bool]$highContrast=$false) {
 InitializeInput
 InitializeOcr
 $null=[KakaoInput]::SetThreadDpiAwarenessContext([IntPtr](-4))
 $ownerEl=[System.Windows.Automation.AutomationElement]::FromHandle($ownerHwnd)
 $ctrlEl=[System.Windows.Automation.AutomationElement]::FromHandle($ctrlHwnd)
 if($ownerEl.Current.ProcessId -ne $processId -or $ctrlEl.Current.ProcessId -ne $processId -or
    ($ownerHwnd -ne $ctrlHwnd -and -not [KakaoInput]::IsChild($ownerHwnd,$ctrlHwnd)) -or -not [KakaoInput]::IsWindowVisible($ctrlHwnd) -or
    [KakaoInput]::IsIconic($ownerHwnd)){throw 'Control is unavailable for OCR'}
 $rect=[KakaoInput+Rect]::new()
 if(-not [KakaoInput]::GetClientRect($ctrlHwnd,[ref]$rect) -or $rect.Right -lt 1 -or $rect.Bottom -lt 1 -or
    [Math]::Max($rect.Right,$rect.Bottom) -gt 4096){throw 'Control size is unavailable'}
 $pwFlags=if($ownerHwnd -eq $ctrlHwnd){[uint32]2}else{[uint32]3}
 $bitmap=[KakaoInput]::CaptureRendered($ctrlHwnd,$rect.Right,$rect.Bottom,$pwFlags)
 $scaled=$null
 try {
  $ratioX=[double]$bitmap.Width/[double]$rect.Right
  $ratioY=[double]$bitmap.Height/[double]$rect.Bottom
  $s=[Math]::Max(1,$scale)
  $effX=$ratioX*[double]$s
  $effY=$ratioY*[double]$s
  if($s -gt 1){
   $scaled=[Drawing.Bitmap]::new($bitmap.Width*$s,$bitmap.Height*$s)
   $gs=[Drawing.Graphics]::FromImage($scaled)
   try {
    $gs.InterpolationMode=[Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $gs.DrawImage($bitmap,0,0,$scaled.Width,$scaled.Height)
   } finally {$gs.Dispose()}
   if($highContrast){[KakaoInput]::BinarizeDarkText($scaled)}
   $rawLines=@(RecognizeBitmapLines $scaled)
  } else {
   if($highContrast){[KakaoInput]::BinarizeDarkText($bitmap)}
   $rawLines=@(RecognizeBitmapLines $bitmap)
  }
  if([Math]::Abs($effX - 1.0) -gt 0.01 -or [Math]::Abs($effY - 1.0) -gt 0.01){
   $rawLines | ForEach-Object {
    @{
     text=$_.text
     x=[int][Math]::Round($_.x/$effX); y=[int][Math]::Round($_.y/$effY)
     w=[int][Math]::Round($_.w/$effX); h=[int][Math]::Round($_.h/$effY)
     cx=[int][Math]::Round($_.cx/$effX); cy=[int][Math]::Round($_.cy/$effY)
     words=@($_.words | ForEach-Object {@{text=$_.text;x=$_.x/$effX;y=$_.y/$effY;w=$_.w/$effX;h=$_.h/$effY}})
    }
   }
  } else {
   $rawLines
  }
 } finally {
  if($scaled){$scaled.Dispose()}
  $bitmap.Dispose()
 }
}
function FindOcrRoomMatch($lines, [string]$roomName) {
 $normRoom=($roomName -replace '\s+','')
 $normHasTrailingDigits=($normRoom -match '\d+$')
 $badgeRx='^(?:나|0|O|o|◎|○|●|•)$'
 $section=$null
 $tier1=@()
 $tier2=@()
 $tier3=@()
 foreach($ln in $lines){
  $rawText=[string]$ln.text
  $trimmed=$rawText.Trim()
  if(-not $trimmed){continue}
  if($trimmed -cmatch '^(채팅방|친구|메시지|통합검색|Chats|Friends|Messages)$'){
   if($trimmed -in '채팅방','Chats'){$section='chats'}
   elseif($trimmed -in '친구','Friends'){$section='friends'}
   elseif($trimmed -in '메시지','Messages'){$section='messages'}
   else{$section='other'}
   continue
  }
  if($section -eq 'messages'){continue}
  $words=@($ln.words)
  if(-not $words.Count){continue}
  $titleWords=@()
  $prevRight=$null
  foreach($wd in $words){
   $wt=[string]$wd.text
   if($null -ne $prevRight -and ($wd.x - $prevRight) -gt [Math]::Max(14.0,$wd.h*1.4)){break}
   if($titleWords.Count -gt 0 -and $wt -match '^(오전|오후|어제|오늘|Yesterday|Today|\d{1,2}:\d{2}|\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}|\d{1,2}월)$'){break}
   $titleWords+=$wd
   $prevRight=$wd.x+$wd.w
  }
  if(-not $titleWords.Count){$titleWords=@($words[0])}
  $primaryCands=@(
   @{words=$words;text=$trimmed},
   @{words=$titleWords;text=(($titleWords | ForEach-Object {$_.text}) -join ' ').Trim()}
  )
  $strippedCands=@()
  if($titleWords.Count -ge 2 -and $titleWords[0].text -cmatch $badgeRx){
   $noBadge=@($titleWords[1..($titleWords.Count-1)])
   $strippedCands+=@{words=$noBadge;text=(($noBadge | ForEach-Object {$_.text}) -join ' ').Trim()}
  }
  if(-not $normHasTrailingDigits -and $titleWords.Count -ge 2 -and $titleWords[$titleWords.Count-1].text -match '^\d+$'){
   $noCount=@($titleWords[0..($titleWords.Count-2)])
   $strippedCands+=@{words=$noCount;text=(($noCount | ForEach-Object {$_.text}) -join ' ').Trim()}
   if($noCount.Count -ge 2 -and $noCount[0].text -cmatch $badgeRx){
    $noBoth=@($noCount[1..($noCount.Count-1)])
    $strippedCands+=@{words=$noBoth;text=(($noBoth | ForEach-Object {$_.text}) -join ' ').Trim()}
   }
  }
  $matchedTier=0
  $matchedWords=$null
  foreach($cand in $primaryCands){
   if($cand.text -ceq $roomName){$matchedTier=1;$matchedWords=$cand.words;break}
  }
  if($matchedTier -eq 0){
   foreach($cand in $strippedCands){
    if($cand.text -ceq $roomName){$matchedTier=2;$matchedWords=$cand.words;break}
   }
  }
  if($matchedTier -eq 0){
   foreach($cand in ($primaryCands + $strippedCands)){
    if(($cand.text -replace '\s+','') -ceq $normRoom){$matchedTier=3;$matchedWords=$cand.words;break}
   }
  }
  if($matchedTier -gt 0){
   $minX=($matchedWords | ForEach-Object {$_.x} | Measure-Object -Minimum).Minimum
   $maxX=($matchedWords | ForEach-Object {$_.x+$_.w} | Measure-Object -Maximum).Maximum
   $minY=($matchedWords | ForEach-Object {$_.y} | Measure-Object -Minimum).Minimum
   $maxY=($matchedWords | ForEach-Object {$_.y+$_.h} | Measure-Object -Maximum).Maximum
   $entry=@{
    text=$trimmed; section=$section
    cx=[int][Math]::Round(($minX+$maxX)/2.0); cy=[int][Math]::Round(($minY+$maxY)/2.0)
   }
   if($matchedTier -eq 1){$tier1+=$entry}
   elseif($matchedTier -eq 2){$tier2+=$entry}
   else{$tier3+=$entry}
  }
 }
 $pool=@(if($tier1.Count){$tier1}elseif($tier2.Count){$tier2}else{$tier3})
 if($pool.Count -gt 1){
  $chatPool=@($pool | Where-Object {$_.section -eq 'chats'})
  if($chatPool.Count -gt 0){$pool=$chatPool}
 }
 if($pool.Count -gt 1){return @{ambiguous=$true;count=$pool.Count}}
 if($pool.Count -eq 1){return @{match=$pool[0]}}
 return @{}
}
function LegacyChatListOcr {
 $ctrlHwnd=[IntPtr]$request.list_hwnd
 $rect=[KakaoInput+Rect]::new()
 $listW=if([KakaoInput]::GetClientRect($ctrlHwnd,[ref]$rect)){$rect.Right}else{450}
 $lines=@(CaptureControlLines ([IntPtr]$request.hwnd) $ctrlHwnd ([int]$request.process_id))
 $badgeRx='^(?:나|0|O|o|◎|○|●|•)$'
 $filtered=@($lines | Where-Object {
  $t=([string]$_.text).Trim()
  $t -and -not ($listW -gt 240 -and $_.x -lt 95 -and $_.h -le 13)
 } | Sort-Object {$_.y},{$_.x})
 $entries=@()
 $current=$null
 foreach($ln in $filtered){
  $raw=([string]$ln.text).Trim()
  if($ln.x -gt [Math]::Max(260,[int]($listW*0.65))){
   if($raw -match '^(?:오전|오후|어제|오늘|Yesterday|Today|\d{1,2}:\d{2}|\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}|\d{1,2}월)'){
    if($current -and [Math]::Abs($ln.y - $current.y) -le 18 -and -not $current.time){$current.time=$raw}
   }
   continue
  }
  if($current -and $ln.x -gt ($current.x + $current.w) -and [Math]::Abs($ln.y - $current.y) -le 12 -and $raw -match '^\d+$'){
   $current.badge=$raw
   continue
  }
  if($current -and ($ln.y - $current.y) -ge 16 -and ($ln.y - $current.y) -le 40 -and $ln.x -ge 90 -and -not $current.preview){
   $current.preview=$raw
   continue
  }
  $words=@($ln.words)
  if($words.Count -ge 2 -and $words[0].text -cmatch $badgeRx){$words=@($words[1..($words.Count-1)])}
  $titleWords=@()
  $prevRight=$null
  foreach($wd in $words){
   $wt=[string]$wd.text
   if($null -ne $prevRight -and ($wd.x - $prevRight) -gt [Math]::Max(14.0,$wd.h*1.4)){break}
   if($titleWords.Count -gt 0 -and $wt -match '^(오전|오후|어제|오늘|Yesterday|Today|\d{1,2}:\d{2}|\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}|\d{1,2}월)$'){break}
   $titleWords+=$wd
   $prevRight=$wd.x+$wd.w
  }
  $title=if($titleWords.Count){(($titleWords | ForEach-Object {$_.text}) -join ' ').Trim()}else{$raw}
  $current=@{title=$title;raw_label=$raw;preview=$null;time=$null;badge=$null;x=$ln.x;y=$ln.y;w=$ln.w;h=$ln.h}
  $entries+=$current
 }
 @{chats=@($entries);read_method='win32_ocr'}
}
# Read a legacy list in memory without exposing other windows or the room's draft.
function LegacyOcrLines {
 InitializeInput
 InitializeOcr
 $null=[KakaoInput]::SetThreadDpiAwarenessContext([IntPtr](-4))
 $null=FileRoom
 $h=[IntPtr]$request.list_hwnd
 $field=[System.Windows.Automation.AutomationElement]::FromHandle($h)
 if($field.Current.ProcessId -ne $request.process_id -or $field.Current.ClassName -ne 'EVA_VH_ListControl_Dblclk' -or
    -not [KakaoInput]::IsChild([IntPtr]$request.hwnd,$h) -or -not [KakaoInput]::IsWindowVisible($h) -or
    [KakaoInput]::IsIconic([IntPtr]$request.hwnd)){throw 'Message list is unavailable for OCR'}
 $rect=[KakaoInput+Rect]::new()
 if(-not [KakaoInput]::GetClientRect($h,[ref]$rect) -or $rect.Right -lt 1 -or $rect.Bottom -lt 1 -or
    [Math]::Max($rect.Right,$rect.Bottom) -gt 4096){throw 'Message list size is unavailable'}
 $bitmap=[Drawing.Bitmap]::new($rect.Right,$rect.Bottom)
 try {
  $g=[Drawing.Graphics]::FromImage($bitmap)
  try {
   $dc=$g.GetHdc()
   try {if(-not [KakaoInput]::PrintWindow($h,$dc,3)){throw 'Message list capture failed'}} finally {$g.ReleaseHdc($dc)}
  } finally {$g.Dispose()}
  $lines=@(RecognizeBitmap $bitmap)
  $null=FileRoom
  if(-not [KakaoInput]::IsChild([IntPtr]$request.hwnd,$h)){throw 'Message list changed during OCR'}
  @{lines=$lines}
 } finally {
  $bitmap.Dispose()
 }
}
function RunRequest($request) {
 $script:dispatched=$false
 $script:roomHwnd=$null
 $script:targetHwnd=$null
 $previousWindow=[IntPtr]::Zero
 try {
 if($request.action -in 'send','copy','image','file','mention','download','open','legacy_open_result','legacy_select_mention','legacy_image_confirm','legacy_photo_ocr_confirm','legacy_send_button'){
  InitializeInput
  $null=[KakaoInput]::SetThreadDpiAwarenessContext([IntPtr](-4))
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
  'legacy_ocr' {LegacyOcrLines}
  'legacy_chat_list_ocr' {LegacyChatListOcr}
  'legacy_open_result' {LegacyOpenResult}
  'legacy_select_mention' {LegacyMention}
  'legacy_image_confirm' {
   $w=FileRoom
   $preview=WaitFor {OwnedPopup} 350
   if(-not $preview){
    InitializeForms
    if(-not [System.Windows.Forms.Clipboard]::ContainsImage()){throw 'Clipboard image unavailable for paste'}
    Front $w
    $edits=@(Children $w | Where-Object {$_.Current.ClassName -eq 'RICHEDIT50W' -and -not $_.Current.IsOffscreen -and $_.Current.IsEnabled})
    if($edits.Count -ge 1){try {$edits[0].SetFocus()} catch {}}
    $script:dispatched=$true
    Ctrl 86
    $preview=WaitFor {OwnedPopup} 2200
   }
   if(-not $preview){throw 'Cannot verify photo preview in the exact room'}
   try {
    $script:dispatched=$true
    LegacyImageConfirm
   } catch {
    if(ConfirmLegacyAttachmentPopup $preview 'photo'){
     @{sent=$true;confirmation='verified_photo_preview_closed'}
    } else {
     throw
    }
   }
  }
  'legacy_photo_ocr_confirm' {
   $null=FileRoom
   $preview=WaitFor {OwnedPopup} 2500
   if(-not $preview){throw 'Cannot verify photo preview in the exact room'}
   if(-not (ConfirmLegacyAttachmentPopup $preview 'photo')){
    throw 'Photo send unconfirmed'
   }
   @{sent=$true;confirmation='verified_photo_preview_closed'}
  }
  'file' {SendFile}
  'health' { $w=Main; @{running=$true;hwnd=$w.Current.NativeWindowHandle;pid=$request.process_id} }
  'rooms' {
   if('KakaoUia' -as [type]){
    @{rooms=@([KakaoUia]::GetRooms([int]$request.process_id) | ForEach-Object {@{title=$_.Title;hwnd=$_.Hwnd}})}
   } else {
    @{rooms=@(Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow'} | ForEach-Object {@{title=$_.Current.Name;hwnd=$_.Current.NativeWindowHandle}})}
   }
  }
  'chats' {
   $w=Main; ChatTab $w
   $chats=@([KakaoUia]::FindChatListEntries($w) | ForEach-Object {
    @{title=$_.Title;name=$_.Name;raw_label=$_.RawLabel;is_offscreen=$_.IsOffscreen}
   })
   @{chats=$chats}
  }
  'participants' {
   $w=if($request.qt){Room $request.room}else{FileRoom}
   $names=@()
   $popup=OwnedPopup
   if($popup){$names=@([KakaoUia]::FindParticipantNames($popup,$false))}
   if(-not $names.Count -and $request.qt){$names=@([KakaoUia]::FindParticipantNames($w,$true))}
   if(-not $request.qt -and -not $names.Count){
    if($popup){
     $phwnd=[IntPtr]$popup.Current.NativeWindowHandle
     $ocrText=''
     if($phwnd -ne [IntPtr]::Zero){
      try {$ocrText=(@(CaptureControlLines $phwnd $phwnd $request.process_id 1) | ForEach-Object {$_.text}) -join ' / '} catch {}
     }
     throw "Room has an open dialog (hwnd=$phwnd class=$($popup.Current.ClassName) name='$($popup.Current.Name)' text='$ocrText'); close it first"
    }
    throw 'Legacy drawer exposes no UIA participants'
   }
   $wasOpen=$names.Count -gt 0
   $drawerBtn=$null
   $openedPopup=$null
   if(-not $wasOpen){
    $drawerBtn=[KakaoUia]::FindDrawerButton($w)
    if(-not $drawerBtn){throw 'Participant drawer or button not found in target room'}
    $invoked=TryInvoke $drawerBtn
    if(-not $invoked){
     $tog=$null
     if($drawerBtn.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$tog)){
      $tog.Toggle()
      $invoked=$true
     }
    }
    if(-not $invoked){throw 'Could not open participant drawer'}
    $deadline=[DateTime]::UtcNow.AddMilliseconds(1500)
    do {
     $openedPopup=OwnedPopup
     if($openedPopup){$names=@([KakaoUia]::FindParticipantNames($openedPopup,$false))}
     if(-not $names.Count){$names=@([KakaoUia]::FindParticipantNames($w,$true))}
     if($names.Count){break}
     Start-Sleep -Milliseconds 60
    } while([DateTime]::UtcNow -lt $deadline)
    if($openedPopup){
     $winPat=$null
     if($openedPopup.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$winPat)){
      try {$winPat.Close()} catch {}
     }
    } elseif($drawerBtn) {
     $tog=$null
     if($drawerBtn.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern,[ref]$tog)){
      try {$tog.Toggle()} catch {}
     } else {
      $null=TryInvoke $drawerBtn
     }
    }
   }
   $participants=@($names | ForEach-Object {
    $raw=$_
    if($raw -match '^(나|방장|부방장|Me|Owner|Admin), (.+)$'){
     @{name=$Matches[2];raw_label=$raw;role=$Matches[1]}
    } else {
     @{name=$raw;raw_label=$raw;role=$null}
    }
   })
   @{room_name=$w.Current.Name;participants=$participants;count=$participants.Count}
  }
  'inspect' {
   $maxDepth=if($request.max_depth -and [int]$request.max_depth -gt 0){[Math]::Min([int]$request.max_depth,10)}else{5}
   $targets=if($request.room){@((Room $request.room))}else{@(Windows)}
   $trees=@()
   $textParts=@()
   foreach($t in $targets){
    $dump=[KakaoUia]::InspectTree($t,$maxDepth,600)
    $trees+=@{hwnd=$t.Current.NativeWindowHandle;title=$t.Current.Name;class_name=$t.Current.ClassName;node=$dump.node;truncated=$dump.truncated}
    $textParts+=$dump.tree_text
   }
   @{diagnostic=$true;room_name=if($request.room){$request.room}else{''};max_depth=$maxDepth;windows=$trees;tree_text=($textParts -join "`n")}
  }
  'close' {
   $w=Room $request.room
   AssertRoomReady $w
   $hwnd=$w.Current.NativeWindowHandle
   $winPattern=$w.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern)
   $winPattern.Close()
   $closed=$false
   $deadline=[DateTime]::UtcNow.AddMilliseconds(2000)
   while([DateTime]::UtcNow -lt $deadline){
    if(@(Windows | Where-Object {$_.Current.NativeWindowHandle -eq $hwnd}).Count -eq 0){$closed=$true;break}
    Start-Sleep -Milliseconds 50
   }
   if(-not $closed){throw 'Target room window did not close'}
   @{closed=$true;hwnd=$hwnd;room_name=$request.room}
  }
  'open' {
   $existing=@(if('KakaoUia' -as [type]){[KakaoUia]::FindChatWindows([int]$request.process_id,$request.room)}else{Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow' -and $_.Current.Name -ceq $request.room}})
   if($existing.Count -eq 1 -and -not $request.self_only){@{hwnd=$existing[0].Current.NativeWindowHandle}; break}
   if($existing.Count -gt 1){throw 'Multiple open rooms have this name'}
   $w=Main; ChatTab $w
   $field=if('KakaoUia' -as [type]){[KakaoUia]::FindByClassPrefix($w,'TextField') | Select-Object -First 1}else{Children $w | Where-Object {$_.Current.ClassName -like 'TextField*'} | Select-Object -First 1}
   if(-not $field){
    $searchBtnCond=[System.Windows.Automation.OrCondition]::new(
     [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'통합검색 Ctrl+F'),
     [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'Search Ctrl+F')
    )
    $button=$w.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$searchBtnCond)
    $lookup={if('KakaoUia' -as [type]){[KakaoUia]::FindByClassPrefix($w,'TextField') | Select-Object -First 1}else{Children $w | Where-Object {$_.Current.ClassName -like 'TextField*'} | Select-Object -First 1}}
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
   if($null -eq $previousQuery){$previousQuery=''}
   try {
    if($previousQuery -cne $request.room){$searchValue.SetValue($request.room)}
    $namePattern='^(?:(?:팀채팅|그룹채팅|나와의 채팅), )?'+[regex]::Escape($request.room)+', '
    if($request.self_only){$namePattern='^나와의 채팅, '+[regex]::Escape($request.room)+', '}
    $matches=@(WaitFor {
     if('KakaoUia' -as [type]){[KakaoUia]::FindMatchingSearchItems($w,$request.room,$namePattern,[bool]$request.self_only)}
     else{Children $w | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::ListItem -and (($_.Current.Name -ceq $request.room -and -not $request.self_only) -or $_.Current.Name -cmatch $namePattern)}}
    } 3000)
    if($matches.Count -ne 1){throw "Expected one exact search result; found $($matches.Count)"}
    $lookup={if('KakaoUia' -as [type]){[KakaoUia]::FindChatWindows([int]$request.process_id,$request.room)}else{Windows | Where-Object {$_.Current.ClassName -match 'ChatWindow' -and $_.Current.Name -ceq $request.room}}}
    $opened=& $lookup
    if(-not $opened -and (TryInvoke $matches[0])){$opened=WaitFor $lookup 1000}
    if(-not $opened){
     Front $w
     if(-not (& $lookup)){
      $item=$matches[0]; $rect=$item.Current.BoundingRectangle
      $x=[int]($rect.Left+$rect.Width/2); $y=[int]($rect.Top+$rect.Height/2)
      AssertHit $item $x $y
      if($searchValue.Current.Value -cne $request.room -or ($item.Current.Name -cnotmatch $namePattern -and
         ($request.self_only -or $item.Current.Name -cne $request.room))){throw 'Room search result changed'}
      Click $item $true
     }
     $opened=WaitFor $lookup 3000
    }
   } finally {
    if($previousQuery -cne $request.room -and $searchValue.Current.Value -ceq $request.room){$searchValue.SetValue($previousQuery)}
   }
   $opened=Room $request.room
   @{hwnd=$opened.Current.NativeWindowHandle}
  }
  'image' {
   $w=Room $request.room; Front $w; $i=InputBox $w
   AssertDraft $i ''
   if([KakaoUia]::FindByClassPrefix($w,'ClipboardImageWindow').Count){throw 'An image preview is already open'}
   $i.SetFocus()
   if(-not [System.Windows.Automation.Automation]::Compare($i,[System.Windows.Automation.AutomationElement]::FocusedElement)){throw 'Chat input lost focus'}
   if([KakaoInput]::GetClipboardSequenceNumber() -ne $request.sequence){throw 'Clipboard image changed'}
   $null=Room $request.room
   $script:dispatched=$true
   Ctrl 86
   $dialogs=@(WaitFor {[KakaoUia]::FindByClassPrefix($w,'ClipboardImageWindow')} 1500)
   if($dialogs.Count -ne 1){throw 'Unique image preview not found in target room'}
   $button=SendButton $dialogs[0]
   $null=Room $request.room
   $script:dispatched=$true
   Invoke $button
   $deadline=[DateTime]::UtcNow.AddMilliseconds(1500)
   while([KakaoUia]::FindByClassPrefix($w,'ClipboardImageWindow').Count -gt 0 -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 50}
   if([KakaoUia]::FindByClassPrefix($w,'ClipboardImageWindow').Count){throw 'Image send outcome uncertain; do not retry automatically'}
   @{sent=$true}
  }
  'download' {
   $w=Room $request.room
   $photos=@([KakaoUia]::FindByClassPrefix($w,'ChatLogContentPhoto') | Sort-Object {$_.Current.BoundingRectangle.Top})
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
   $scroll=MessageListScroll $w $items
   $atEnd=if($scroll){(-not $scroll.Current.VerticallyScrollable -or $scroll.Current.VerticalScrollPercent -ge 99.9)}else{$null}
   $scrollPages=if($request.scroll_pages){[int]$request.scroll_pages}else{0}
   if($scrollPages -gt 0 -and $scroll -and $scroll.Current.VerticallyScrollable){
    $pagesBottomToTop=@(,$messages)
    $prevPct=$scroll.Current.VerticalScrollPercent
    for($p=0;$p -lt $scrollPages;$p++){
     if($prevPct -le 0.01){break}
     $scroll.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount,[System.Windows.Automation.ScrollAmount]::LargeDecrement)
     Start-Sleep -Milliseconds 150
     $pageItems=@(ChatItems $w)
     $pageMsgs=@(Messages $pageItems)
     $pagesBottomToTop+=,$pageMsgs
     $newPct=$scroll.Current.VerticalScrollPercent
     if($newPct -ge ($prevPct - 0.01)){break}
     $prevPct=$newPct
    }
    try {$scroll.SetScrollPercent(-1,100)} catch {}
    Start-Sleep -Milliseconds 80
    $atEnd=(-not $scroll.Current.VerticallyScrollable -or $scroll.Current.VerticalScrollPercent -ge 99.9)
    $pagesChrono=@()
    for($idx=$pagesBottomToTop.Count-1;$idx -ge 0;$idx--){$pagesChrono+=,$pagesBottomToTop[$idx]}
    $messages=@(MergeScrollPages $pagesChrono)
   }
   $unknown=@('member_count')
   if($messages | Where-Object {-not $_.sender}){$unknown+='sender'}
   if($messages | Where-Object {-not $_.date}){$unknown+='date'}
   @{room_name=$w.Current.Name;hwnd=$w.Current.NativeWindowHandle;process_id=$request.process_id;member_count=$null;messages=$messages;dates=@();partial=$true;read_method='uia';unknown_fields=$unknown;at_end=$atEnd}
  }
  'copy' {
   $w=Room $request.room; Front $w
   $items=@(ChatItems $w)
   if(-not $items.Count){@{raw_text=''};break}
   $message=$items | Where-Object {(VisibleCenter $w $_) -and $_.Current.Name -notmatch '^날짜 ' -and $_.Current.Name -notmatch '^(?:Date )?(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}$'} | Select-Object -Last 1
   if(-not $message){throw 'No visible message to copy'}
   $focused=$false
   if($message.Current.IsKeyboardFocusable){
    try {
     $message.SetFocus()
     $focused=[System.Windows.Automation.Automation]::Compare($message,[System.Windows.Automation.AutomationElement]::FocusedElement)
    } catch {$focused=$false}
   }
   if(-not $focused){Click $message}
   InitializeForms
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
   if($v.Current.Value){throw 'Chat has an existing draft'}
   $beforeMsgs=@()
   $beforeAtEnd=$null
   if($request.verify_bubble){
    $snapItems=@(ChatItems $w)
    $beforeAtEnd=MessageListAtEnd $w $snapItems
    if($beforeAtEnd -eq $false){
     $scroll=MessageListScroll $w $snapItems
     if($scroll){
      try {$scroll.SetScrollPercent(-1,100); Start-Sleep -Milliseconds 80} catch {}
      $snapItems=@(ChatItems $w)
      $beforeAtEnd=MessageListAtEnd $w $snapItems
     }
    }
    if($snapItems.Count){$beforeMsgs=@(Messages $snapItems)}
   }
   $v.SetValue($request.text)
   if($v.Current.Value -cne $request.text){throw 'Input verification failed'}
   if(([KakaoUia]::FindByClassPrefix($w,'ClipboardImageWindow')).Count){throw 'Image preview is open in target room'}
   AssertRoomReady $w
   $deadline=[DateTime]::UtcNow.AddMilliseconds(400)
   $button=$null
   while($true){
    $buttons=@([KakaoUia]::FindSendButtons($w))
    if($buttons.Count -eq 1){$button=$buttons[0];break}
    if($buttons.Count -gt 1 -or [DateTime]::UtcNow -ge $deadline){throw 'Unique enabled send button not found'}
    Start-Sleep -Milliseconds 40
   }
   AssertDraft $i $request.text
   $null=Room $request.room
   $script:dispatched=$true
   Invoke $button
   $deadline=[DateTime]::UtcNow.AddMilliseconds(1500)
   $cleared=$false
   while($true){
    $curVal=$null
    try {$curVal=(Value $i).Current.Value} catch {$curVal=(Value (InputBox $w)).Current.Value}
    if([string]::IsNullOrEmpty($curVal)){$cleared=$true;break}
    if([DateTime]::UtcNow -ge $deadline){break}
    Start-Sleep -Milliseconds 40
   }
   if(-not $cleared){throw 'Send outcome uncertain; do not retry automatically'}
   if($request.verify_bubble -and $beforeAtEnd -ne $false){
    $expectedText=$request.text.Replace("`r`n","`n").Replace("`r","`n")
    $bubbleDeadline=[DateTime]::UtcNow.AddMilliseconds(1500)
    $matchedBubble=$null
    while($true){
     $curItems=@(ChatItems $w)
     $curAtEnd=MessageListAtEnd $w $curItems
     if($curAtEnd -ne $false -and $curItems.Count){
      $afterMsgs=@(Messages $curItems)
      $matchedBubble=FindNewOutgoingBubble $beforeMsgs $afterMsgs $expectedText
      if($matchedBubble){break}
     }
     if([DateTime]::UtcNow -ge $bubbleDeadline){break}
     Start-Sleep -Milliseconds 40
    }
    if($matchedBubble){@{sent=$true;confirmation='new_room_bubble';bubble=$matchedBubble};break}
    throw 'New matching bubble not verified; inspect the room before retrying'
   }
   $sendResult=@{sent=$true;confirmation='draft_cleared'}
   if($request.verify_bubble -and $beforeAtEnd -eq $false){$sendResult.at_end=$false}
   $sendResult
  }
  default {throw "Unknown action $($request.action)"}
 }
 @{success=$true;data=$result} | ConvertTo-Json -Depth 32 -Compress
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
InitializeInput
InitializeUia
InitializeForms
$null=[KakaoInput]::SetThreadDpiAwarenessContext([IntPtr](-4))
try { InitializeOcr } catch {}
while($null -ne ($line=[Console]::ReadLine())){
 try {
  $json=RunRequest (ConvertFrom-Json -InputObject $line)
  [Console]::WriteLine($json)
 } catch {
  [Console]::WriteLine((@{success=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress))
 }
}
