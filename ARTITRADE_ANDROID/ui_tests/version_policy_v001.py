"""VersionName is a clean three-component display label (not Android versionCode)."""
import re

def next_app_version(current):
    if not isinstance(current,str) or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)",current):
        raise ValueError("VERSION_NAME_MUST_BE_NUMERIC_X_Y_Z")
    major,minor,patch=map(int,current.split("."))
    if patch<9:
        patch+=1
    else:
        patch=0
        if minor<9:
            minor+=1
        else:
            minor=0
            major+=1
    return f"{major}.{minor}.{patch}"
