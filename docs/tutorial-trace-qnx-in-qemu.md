# Tutorial: trace a QNX OS 8.0 program in QEMU and turn its timing into a CI test

This walks through everything Deadline CI's demo does, from an empty machine to a failing test when a real-time loop misses its deadline. It was written on an Apple Silicon Mac, which QNX SDP 8.0 does not support as a host directly, so it also shows how to run the SDP's Linux x86_64 tools there. On a Linux x86_64 machine, skip step 1.

You will:

1. run QNX SDP 8.0's host tools on a Mac (Linux container + Rosetta),
2. install SDP 8.0 from the command line,
3. build a small real-time program with `qcc`,
4. build a bootable QEMU image with `mkqnximage` that runs your program and `tracelogger` on boot,
5. boot it with repeatable timing and pull the decoded trace out over the serial console,
6. check the trace against timing budgets, locally and in CI.

You need a free QNX SDP 8.0 non-commercial licence (https://www.qnx.com/products/everywhere/), Docker, and QEMU.

## 1. A Linux x86_64 environment on Apple Silicon

QNX SDP 8.0's host tools are Linux x86_64 (or Windows) binaries. On Apple Silicon, [colima](https://github.com/abiosoft/colima) can start a Linux VM with Apple's Rosetta translation enabled, and Docker containers for `linux/amd64` then run at close to native speed:

```bash
brew install colima docker qemu
colima start --profile qnx --arch aarch64 --vm-type vz --vz-rosetta --cpu 6 --memory 12 --disk 80
docker --context colima-qnx volume create qnx-home
docker --context colima-qnx run -d --name qnxhost --platform linux/amd64 \
  -v qnx-home:/root -v "$PWD":/work ubuntu:24.04 sleep infinity
docker --context colima-qnx exec qnxhost bash -lc 'apt-get update && apt-get install -y python3 file'
```

Run the later steps inside the container with `docker --context colima-qnx exec -it qnxhost bash`. QEMU itself runs on the Mac (step 5).

Licence note: the non-commercial single-user licence covers your own development machines, including virtual development environments, but not build servers or cloud machines. Keep SDP on your own machine and let CI work on the trace files.

## 2. Install SDP 8.0 without the GUI

Download the QNX Software Center installer for Linux hosts from the myQNX download area (it is a self-extracting `.run` file), then:

```bash
sh qnx-setup-*-linux.run --nox11 -- force-override disable-auto-start agree-to-license-terms ~/qnx/qnxsoftwarecenter
cd ~/qnx/qnxsoftwarecenter/qnxsoftwarecenter
QSC="./qnxsoftwarecenter_clt -myqnx.user you@example.com -myqnx.password ..."
$QSC -addLicenseKey XXXX-XXXX-XXXX-XXXX-XXXX     # the key from the myQNX License Manager
$QSC -mirror                                     # without this, the baseline install misses packages
$QSC -installBaseline com.qnx.qnx800 -destination ~/qnx800
source ~/qnx800/qnxsdp-env.sh                    # puts qcc, mkqnximage and traceprinter on PATH
```

Two things that cost time: the licence must be accepted and deployed to your own account in the License Manager before the key works, and installing the baseline right after a partial metadata sync fails with "requires 'package; com.qnx.qnx800.target' but it could not be found"; `-mirror` fixes it.

## 3. A real-time program to measure

The demo's control loop is the classic QNX pattern: a thread blocks in `MsgReceivePulse()` until a periodic timer delivers a pulse, does its work, and blocks again. Its wake-up latency is the time from leaving the RECEIVE-blocked state to running.

```c
SIGEV_PULSE_INIT(&ev, coid, LOOP_PRIORITY, PULSE_CODE_TICK, 0);
timer_create(CLOCK_MONOTONIC, &ev, &timer);
timer_settime(timer, 0, &(struct itimerspec){ .it_value = {0, 1000000}, .it_interval = {0, 1000000} }, NULL);
for (;;) {
    struct _pulse pulse;
    if (MsgReceivePulse(chid, &pulse, sizeof pulse, NULL) == 0 && pulse.code == PULSE_CODE_TICK)
        spin_us(50);   /* the control work */
}
```

The full sources are in `scenario/`: `deadline-demo.c` (the loop plus a message server), `deadline-client.c` (a `MsgSend()` client) and `ai-worker.c` (a CPU-heavy job whose priority you can set). Build them for x86_64:

```bash
for p in deadline-demo deadline-client ai-worker; do
  qcc -Vgcc_ntox86_64 -O2 -Wall -o build/x86_64/$p scenario/$p.c
done
```

## 4. A bootable image that traces on start-up

`mkqnximage` builds a QEMU image from options plus "snippets" in a `local/snippets` folder next to where you run it. Two snippets are enough:

`local/snippets/system_files.custom` adds the tracing tools and your programs to the system partition:

```
bin/tracelogger=usr/sbin/tracelogger
bin/traceprinter=usr/bin/traceprinter
[perms=755] deadline/deadline-demo=/work/build/x86_64/deadline-demo
[perms=755] deadline/run-scenario.sh=/work/scenario/run-scenario.sh
```

`local/snippets/post_start.custom` runs a script once the system is up:

```
/system/deadline/autorun.sh &
```

The script starts the programs, captures two seconds of kernel events, decodes them on the target and prints them between markers on the console:

```sh
tracelogger -n 0 -s 2 -f /tmp/trace.kev
traceprinter -n -p "c:0x%016c CPU:%02C %-16Z:%-18z" -f /tmp/trace.kev > /tmp/trace.txt
echo "===DEADLINE-TRACE-BEGIN healthy==="; cat /tmp/trace.txt; echo "===DEADLINE-TRACE-END healthy==="
```

The `-p` format keeps the full 64-bit cycle count, so no counter wraps need undoing. Build the image (x86_64; the default kernel, `procnto-smp-instr`, is the instrumented one tracing needs):

```bash
mkqnximage --type=qemu --arch=x86_64 --cpu=1 --ram=1G --noprompt --force --build
```

Why x86_64 on an ARM Mac: the aarch64 QEMU "virt" start-up code is not in SDP 8.0's base packages, while the x86_64 start-up is.

## 5. Boot with repeatable timing and collect the trace

```bash
qemu-system-x86_64 -display none -monitor none -serial file:serial.log \
  -m 1G -smp 1 -cpu max -icount shift=0,sleep=off \
  -drive file=output/disk-qemu.vmdk,if=ide -kernel output/ifs.bin &
# wait for the end marker, stop QEMU, then cut the trace out of the log:
awk '/===DEADLINE-TRACE-BEGIN healthy===/{f=1;next}/===DEADLINE-TRACE-END healthy===/{f=0}f' serial.log > healthy.txt
```

`-icount shift=0,sleep=off` is the important flag. Without it, x86_64 emulated on ARM lets the guest's timer interrupts and its cycle counter drift apart: a 1 ms periodic timer showed up as a mix of 1.3 and 2.6 ms intervals. With instruction counting, both come from one virtual clock: the clock interrupt arrives every 1,000 us to within 0.2 us, and two boots of the same image agree to within half a microsecond at p99. The numbers are emulated time, which suits regression tests: what changes from one run to the next is mostly what you changed.

No networking is needed: the trace travels over the serial console. `target/run-qemu.sh` in this repository does all of step 5.

## 6. Turn the trace into tests

Declare budgets for the threads you care about (find pids and tids with `deadline threads healthy.txt`):

```toml
[[budget]]
name = "Control loop wakes up within 150 us of its 1 ms timer pulse"
type = "latency"
thread = { process = "deadline-demo", tid = 2 }
from_state = "THRECEIVE"
percentile = 99
max_us = 150
```

```bash
pip install git+https://github.com/vinit1009/deadline-ci
deadline check healthy.txt   --budgets deadlines.toml                            # exit 0
deadline check regressed.txt --budgets deadlines.toml --svg worst.svg --junit report.xml   # exit 1
```

In the demo, moving the AI worker above the control loop's priority takes the loop's p99 wake-up latency from 1.6 us to about 9,000 us; four of the five budgets fail, and `worst.svg` shows the loop sitting ready while the worker runs.

### Where did the time go?

A late wake-up can come from interrupt handling, from the kernel's timer and pulse delivery, or from scheduling. The trace has an event for each step: in the healthy run, every tick of the loop looks like this (the pid is the demo's):

```
INTERRUPT :INT_DELIVER   intr_id:0x80010000(2147549184)
SYSTEM    :TIMER         pid:364560 tid:0 timer_id:0 flags:0x00000011 ACTIVE TARGET_PROCESS
COMM      :SND_PULSE_EXE scoid:0x00010001 pid:364560
THREAD    :THREADY       pid:364560 tid:2
THREAD    :THRUNNING     pid:364560 tid:2
COMM      :REC_PULSE     scoid:0x00010001 pid:364560
```

A `chain` budget names those stages and measures from the first one to the thread running:

```toml
[[budget]]
name = "Clock interrupt to control loop running within 200 us"
type = "chain"
thread = { process = "deadline-demo", tid = 2 }
from_state = "THRECEIVE"
stages = ["INTERRUPT:INT_DELIVER intr_id=0x80010000", "SYSTEM:TIMER", "COMM:SND_PULSE_EXE"]
percentile = 99
max_us = 200
```

The `intr_id=` filter matters: another interrupt between the clock interrupt and the timer would otherwise be taken as the start of the chain. On the regressed trace the budget fails and splits the worst wake-up into stages: 3.5 us from the interrupt to the timer firing, 0.9 us to the pulse, 0.5 us to ready, and 9,005.7 us from ready to running. The kernel did its part in about 5 us; the fix is the worker's priority, not the timer.

In GitHub Actions:

```yaml
- uses: vinit1009/deadline-ci@v0
  with:
    trace: traces/regressed.txt
    budgets: deadlines.toml
```

The step fails the build, writes JUnit results and adds a table to the job summary. Since SDP stays on your own machine, run the capture there (or on a self-hosted runner on that machine) and let CI check the trace files.
