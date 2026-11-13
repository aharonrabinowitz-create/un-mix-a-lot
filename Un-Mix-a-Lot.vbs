' Double-click to start Un-Mix-a-Lot in its own window, with no console.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
py = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
  MsgBox "Un-Mix-a-Lot isn't installed yet. Run:  python install.py", 16, "Un-Mix-a-Lot"
  WScript.Quit 1
End If
' Desktop shortcut: created if missing, and its icon refreshed every launch (also the old "Stem Split" one)
For Each nm In Array("Un-Mix-a-Lot", "Stem Split")
  lnk = sh.SpecialFolders("Desktop") & "\" & nm & ".lnk"
  If fso.FileExists(lnk) Or nm = "Un-Mix-a-Lot" Then
    Set sc = sh.CreateShortcut(lnk)
    sc.TargetPath = sh.ExpandEnvironmentStrings("%SystemRoot%\System32\wscript.exe")
    sc.Arguments = """" & root & "\Un-Mix-a-Lot.vbs"""
    sc.WorkingDirectory = root
    If fso.FileExists(root & "\icon.ico") Then sc.IconLocation = root & "\icon.ico,0"
    sc.Description = "Un-Mix-a-Lot"
    sc.Save
  End If
Next
If Not fso.FolderExists(root & "\.venv\Lib\site-packages\webview") Then
  sh.Popup "Setting up the app window (one time, about a minute). Un-Mix-a-Lot will open by itself.", 8, "Un-Mix-a-Lot", 64
End If
sh.CurrentDirectory = root
sh.Run """" & py & """ """ & root & "\desktop.py""", 0, False
