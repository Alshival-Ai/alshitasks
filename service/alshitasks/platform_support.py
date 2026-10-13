"""OS boundaries for per-user storage and process exclusion."""
import os
from pathlib import Path

WINDOWS = os.name == "nt"


def user_sid():
    import win32api
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), 8)
    try:
        return win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()


def owned(path):
    if not WINDOWS:
        return Path(path).stat().st_uid == os.getuid()
    import win32security as security
    owner = security.GetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
                                         security.OWNER_SECURITY_INFORMATION).GetSecurityDescriptorOwner()
    return owner == user_sid()


def protect(path, directory=False):
    """Restrict Windows state to this user and SYSTEM, including new children."""
    if not WINDOWS:
        Path(path).chmod(0o700 if directory else 0o600)
        return
    import win32security as security
    acl = security.ACL()
    flags = 3 if directory else 0  # object/container inheritance
    for sid in (user_sid(), security.CreateWellKnownSid(security.WinLocalSystemSid)):
        acl.AddAccessAllowedAceEx(security.ACL_REVISION, flags, 0x1F01FF, sid)
    security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
                                 security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
                                 None, None, acl, None)


def lock_file(stream):
    if WINDOWS:
        import msvcrt
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise BlockingIOError("Service lock is held") from error
    else:
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def windows_home():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Alshival.Ai"


def mcp_command(grant=None):
    import sys
    arguments = ["mcp"] + (["--grant", grant] if grant else [])
    if WINDOWS:
        return {"command": sys.executable, "args": [str(Path(__file__).resolve().parents[1] / "alshitasks-cli.py"), *arguments]}
    return {"command": "alshitasks", "args": arguments}
