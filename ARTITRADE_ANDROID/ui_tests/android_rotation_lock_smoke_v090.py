"""Android 15: user-controlled screen orientation must not kill ARTITRADE.

Runs AFTER existing v084 emulator boot/return and does not touch actual
brokers, user Android data, positions or SQLite.
"""
import time
import android_emulator_smoke_v054 as smoke

def verify_pid(expected,stage):
    pid=smoke.adb("shell","pidof",smoke.PACKAGE,
                  timeout=15,check=False).strip()
    if not pid:
        raise AssertionError("ARTITRADE_PROCESS_DIED_ON_"+stage)
    if pid!=expected:
        raise AssertionError(
            "ARTITRADE_PROCESS_RECREATED_ON_"+stage+
            " old_pid="+expected+" new_pid="+pid)
    smoke.verify_no_fatal("v090_after_"+stage)

def main():
    smoke.adb("shell","am","start","-W","-n",
              smoke.PACKAGE+"/"+smoke.ACTIVITY,timeout=60)
    time.sleep(2.0)
    before=smoke.adb("shell","pidof",smoke.PACKAGE,
                     timeout=15,check=False).strip()
    if not before:
        raise AssertionError("NO_INITIAL_ARTITRADE_PID")
    # A user-locked device must use fullUser, not fullSensor. This test
    # orders rotations through the SYSTEM setting; ARTITRADE itself NEVER
    # requests or overrides screenOrientation and must retain its process.
    smoke.adb("shell","settings","put","system","accelerometer_rotation",
              "0",timeout=12)
    try:
        for rot in (0,1,3,0):
            command=smoke.adb("shell","wm","user-rotation","lock",str(rot),
                              timeout=15,check=False)
            if "unknown" in command.lower() or "error:" in command.lower():
                raise AssertionError("ANDROID_USER_ROTATION_COMMAND_FAILED: "+command)
            time.sleep(2.2)
            verify_pid(before,"SYSTEM_USER_ROTATION_"+str(rot))
        smoke.adb("shell","settings","put","system","accelerometer_rotation",
                  "1",timeout=12)
        smoke.adb("shell","wm","user-rotation","free",timeout=12,check=False)
        time.sleep(1.2)
        verify_pid(before,"AUTOROTATE_ENABLED")
    finally:
        smoke.adb("shell","settings","put","system","accelerometer_rotation",
                  "0",timeout=12,check=False)
        smoke.adb("shell","wm","user-rotation","lock","0",
                  timeout=12,check=False)
    print("V090_ANDROID15_ROTATION_LOCK_AND_PROCESS_CONTINUITY_PASS",flush=True)

if __name__=="__main__":
    main()
